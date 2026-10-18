"""Native OrCAD PST facts and compact, fully expandable query views.

This deliberately supports the single-section PST dialect exercised by Clay R6.
Unsupported physical-pin expressions stop ingestion rather than guess mappings.
Raw attributes and source text remain available; optional identity never gates a
connection. No datasheet interpretation or electrical pass/fail occurs here.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import re


class DatError(ValueError):
    """The input cannot be interpreted reliably within the supported dialect."""


@dataclass(frozen=True)
class Token:
    value: str
    quoted: bool
    line: int


class Reader:
    def __init__(self, text: str, filename: str):
        self.filename = filename
        self.tokens = []
        # Quoted values preserve case, whitespace and literal backslashes.
        token_re = re.compile(r"\s+|\{[^}]*\}|'[^']*'|[A-Za-z0-9_./$+\-]+|[=;:,()]", re.S)
        pos, line = 0, 1
        while pos < len(text):
            match = token_re.match(text, pos)
            if not match:
                raise DatError(f"{filename}:{line}: unsupported token near {text[pos:pos+25]!r}")
            raw = match.group()
            if not raw.isspace() and not raw.startswith("{"):
                self.tokens.append(Token(raw[1:-1] if raw.startswith("'") else raw,
                                         raw.startswith("'"), line))
            line += raw.count("\n")
            pos = match.end()
        self.i = 0

    def peek(self, value=None):
        token = self.tokens[self.i] if self.i < len(self.tokens) else None
        return token if value is None else token is not None and token.value == value and not token.quoted

    def take(self, expected=None, quoted=None):
        token = self.peek()
        if token is None:
            raise DatError(f"{self.filename}: unexpected end of input; expected {expected}")
        if ((expected is not None and (token.value != expected or token.quoted))
                or (quoted is not None and token.quoted != quoted)):
            raise DatError(f"{self.filename}:{token.line}: expected {expected or 'quoted string'}, got {token.value!r}")
        self.i += 1
        return token

    def properties(self, until=None):
        out = {}
        while self.peek() is not None and (until is None or not self.peek(until)):
            key = self.take(quoted=False).value
            self.take("=")
            value = self.take(quoted=True).value
            if key in out and out[key] != value:
                raise DatError(f"{self.filename}: conflicting property {key}")
            out[key] = value
            end = self.take(quoted=False).value
            if end not in (",", ";"):
                raise DatError(f"{self.filename}: expected property terminator")
            if until is None and end == ";":
                break
        return out

    def header(self, kind):
        self.take("FILE_TYPE")
        self.take("=")
        self.take(kind)
        self.take(";")
        if self.peek("DIRECTIVES"):
            self.take()
            self.properties("END_DIRECTIVES")
            self.take("END_DIRECTIVES")
            self.take(";")

    def finish(self):
        self.take("END.")
        if self.peek() is not None:
            raise DatError(f"{self.filename}: trailing content after END.")

    def location(self, first):
        return {"file": self.filename, "start_line": first,
                "end_line": self.tokens[self.i - 1].line}


def _single_pin(value):
    match = re.fullmatch(r"\(\s*([A-Za-z0-9_]+)\s*\)", value)
    if not match:
        raise DatError(f"Unsupported PIN_NUMBER {value!r}; physical mapping requires a supported dialect")
    return match.group(1)


def _nc_pins(value):
    match = re.fullmatch(r"\(\s*([A-Za-z0-9_]+(?:\s*,\s*[A-Za-z0-9_]+)*)\s*\)", value)
    if not match:
        raise DatError(f"Unsupported NC_PINS {value!r}")
    return [part.strip() for part in match.group(1).split(",")]


def _page(rows, offset, limit):
    if type(offset) is not int or type(limit) is not int or offset < 0 or limit < 1:
        raise ValueError("offset must be >= 0 and limit must be >= 1")
    end = min(offset + limit, len(rows))
    return {"total": len(rows), "offset": offset, "items": rows[offset:end],
            "next_offset": end if end < len(rows) else None}


class DatGraph:
    """Parse once per investigation/session and reuse in-memory indices."""

    def __init__(self, directory, *, encoding="utf-8"):
        self.directory = Path(directory)
        self.raw = {name: (self.directory / name).read_text(encoding=encoding)
                    for name in ("pstchip.dat", "pstxprt.dat", "pstxnet.dat")}
        self.primitives, self.components, self.nets = {}, {}, {}
        self.nc_records = []
        self.diagnostics = []
        self._aliases = defaultdict(list)
        self._parse_chips()
        self._parse_parts()
        self._parse_nets()

    @staticmethod
    def _new_pin(number, names=None, declared=False, nc=False, source=None):
        return {"number": number, "names": names or [], "declared": declared,
                "nc_declared": nc, "nc_exported": False, "nets": [],
                "sources": [source] if source else [], "raw_attributes": {}}

    def _parse_chips(self):
        r = Reader(self.raw["pstchip.dat"], "pstchip.dat")
        r.header("LIBRARY_PARTS")
        while r.peek("primitive"):
            start = r.take().line
            name = r.take(quoted=True).value
            r.take(";")
            r.take("pin")
            pins = {}
            while not r.peek("end_pin"):
                name_token = r.take(quoted=True)
                r.take(":")
                # Each pin has one or more semicolon-terminated properties;
                # the next quoted token starts a new pin.
                attrs = {}
                while r.peek() and not r.peek().quoted and not r.peek("end_pin"):
                    for key, value in r.properties().items():
                        if key in attrs and attrs[key] != value:
                            raise DatError(f"pstchip.dat:{name_token.line}: conflicting property {key}")
                        attrs[key] = value
                if "PIN_NUMBER" not in attrs:
                    raise DatError(f"pstchip.dat:{name_token.line}: pin has no physical PIN_NUMBER")
                number = _single_pin(attrs["PIN_NUMBER"])
                if number in pins:
                    raise DatError(f"pstchip.dat: duplicate physical pin {name}.{number}")
                pin = self._new_pin(number, [name_token.value], True, source=r.location(name_token.line))
                pin["raw_attributes"] = attrs
                pins[number] = pin
            r.take("end_pin")
            r.take(";")
            r.take("body")
            attrs = r.properties("end_body")
            r.take("end_body")
            r.take(";")
            r.take("end_primitive")
            r.take(";")
            if "POWER_PINS" in attrs:
                raise DatError("POWER_PINS dialect is not yet supported; cannot certify physical-pin coverage")
            for number in _nc_pins(attrs["NC_PINS"]) if "NC_PINS" in attrs else []:
                if number in pins:
                    pins[number]["nc_declared"] = True
                else:
                    pins[number] = self._new_pin(number, declared=True, nc=True, source=r.location(start))
            if name in self.primitives:
                raise DatError(f"Duplicate primitive {name}")
            self.primitives[name] = {"pins": pins, "attributes": attrs, "source": r.location(start)}
        r.finish()

    def _parse_parts(self):
        r = Reader(self.raw["pstxprt.dat"], "pstxprt.dat")
        r.header("EXPANDEDPARTLIST")
        while r.peek("PART_NAME"):
            start = r.take().line
            ref = r.take(quoted=False).value
            primitive = r.take(quoted=True).value
            r.take(":")
            r.take(";")
            sections = []
            while r.peek("SECTION_NUMBER"):
                r.take()
                number = r.take(quoted=False).value
                path = r.take(quoted=True).value
                r.take(":")
                sections.append({"number": number, "path": path, "attributes": r.properties()})
            if len(sections) != 1 or sections[0]["number"] != "1":
                raise DatError(f"{ref}: multi-section PST mapping is not validated by this adapter")
            if ref in self.components:
                raise DatError(f"Duplicate physical instance {ref}")
            library = self.primitives.get(primitive)
            if library is None:
                self.diagnostics.append({"kind": "undeclared_primitive", "ref": ref})
            self.components[ref] = {
                "ref": ref, "primitive": primitive,
                "attributes": deepcopy(library["attributes"]) if library else {},
                "pins": deepcopy(library["pins"]) if library else {},
                "sections": sections, "source": r.location(start),
                # VALUE='DNP' is retained as evidence, never silently removed.
                "assembly": "unspecified",
            }
        r.finish()

    def _parse_nets(self):
        r = Reader(self.raw["pstxnet.dat"], "pstxnet.dat")
        r.header("EXPANDEDNETLIST")
        while r.peek("NET_NAME"):
            start = r.take().line
            name = r.take(quoted=True).value
            path = r.take(quoted=True).value
            r.take(":")
            attrs = r.properties()
            # Capture 16.6 User Guide p415: NC here aggregates disconnected pins.
            is_nc = name == "NC" and path == "NC" and attrs.get("C_SIGNAL") == "NC"
            if path in self.nets:
                raise DatError(f"Duplicate full net path {path}")
            net = {"name": name, "path": path, "attributes": attrs, "members": []}
            seen_members = set()
            while r.peek("NODE_NAME"):
                node_start = r.take().line
                ref = r.take(quoted=False).value
                number = r.take(quoted=False).value
                node_path = r.take(quoted=True).value
                r.take(":")
                pin_name = r.take(quoted=True).value
                r.take(":")
                r.take(";")
                if (ref, number) in seen_members:
                    raise DatError(f"pstxnet.dat:{node_start}: duplicate endpoint {ref}.{number} on {name}")
                seen_members.add((ref, number))
                if ref not in self.components:
                    self.components[ref] = {"ref": ref, "primitive": None, "attributes": {},
                        "pins": {}, "sections": [], "source": r.location(node_start),
                        "assembly": "unspecified"}
                    self.diagnostics.append({"kind": "undeclared_component", "ref": ref})
                comp = self.components[ref]
                if comp["sections"] and node_path != comp["sections"][0]["path"]:
                    raise DatError(f"pstxnet.dat:{node_start}: occurrence path for {ref} differs from pstxprt.dat")
                if number not in comp["pins"]:
                    comp["pins"][number] = self._new_pin(number)
                    self.diagnostics.append({"kind": "undeclared_pin", "ref": ref, "pin": number})
                pin = comp["pins"][number]
                if pin_name not in pin["names"]:
                    pin["names"].append(pin_name)
                if is_nc:
                    pin["nc_exported"] = True
                elif path not in pin["nets"]:
                    pin["nets"].append(path)
                pin["sources"].append(r.location(node_start))
                net["members"].append({"ref": ref, "pin": number, "name": pin_name,
                                       "path": node_path, "source": r.location(node_start)})
            net["source"] = r.location(start)
            if is_nc:
                self.nc_records.append(net)
            else:
                self.nets[path] = net
                for alias in {name, path, attrs.get("C_SIGNAL", path)}:
                    self._aliases[alias].append(path)
        r.finish()
        for comp in self.components.values():
            for pin in comp["pins"].values():
                if self._pin_status(pin) == "conflicting_records":
                    self.diagnostics.append({"kind": "conflicting_pin_records", "ref": comp["ref"], "pin": pin["number"]})

    @staticmethod
    def _pin_status(pin):
        if len(pin["nets"]) > 1 or (pin["nets"] and (pin["nc_declared"] or pin["nc_exported"])):
            return "conflicting_records"
        if pin["nets"]:
            return "connected"
        if pin["nc_declared"] or pin["nc_exported"]:
            return "unconnected"
        return "connection_unspecified"

    def _resolve_net(self, query):
        if query in self.nets:
            return query
        paths = self._aliases.get(query, [])
        if not paths:
            raise KeyError(f"No electrical net {query!r}; NC export groups are not conductive nets")
        if len(paths) != 1:
            raise KeyError(f"Ambiguous short net name {query!r}; use a full path: {paths}")
        return paths[0]

    def _pin_view(self, pin, detail=False):
        result = {"pin": pin["number"], "names": pin["names"], "state": self._pin_status(pin),
                  "nets": [self.nets[p]["name"] for p in pin["nets"]]}
        if not pin["declared"]:
            result["declaration"] = "absent"
        if any(len(self._aliases[self.nets[p]["name"]]) > 1 for p in pin["nets"]):
            result["net_paths"] = pin["nets"]
        if detail:
            result.update({"net_paths": pin["nets"], "nc_declared": pin["nc_declared"],
                "nc_exported": pin["nc_exported"], "raw_attributes": pin["raw_attributes"],
                "sources": pin["sources"]})
        return result

    def overview(self):
        pins = [p for c in self.components.values() for p in c["pins"].values()]
        return {"components": len(self.components), "physical_pins": len(pins),
                "electrical_nets": len(self.nets),
                "connected_endpoints": sum(len(n["members"]) for n in self.nets.values()),
                "pin_states": dict(Counter(self._pin_status(p) for p in pins)),
                "nc_exported_endpoints": sum(len(n["members"]) for n in self.nc_records),
                "nc_declared_pins": sum(p["nc_declared"] for p in pins),
                "input_diagnostic_count": len(self.diagnostics),
                "next": "inventory enumerates every instance; component/net expand all members"}

    def inventory(self, offset=0, limit=40):
        rows = [{"ref": c["ref"], "type_hint": c["attributes"].get("PART_NAME"),
                 "value": c["attributes"].get("VALUE"), "pins": len(c["pins"])}
                for c in self.components.values()]
        return _page(rows, offset, limit)

    def component(self, ref, *, offset=0, limit=40, detail=False):
        comp = self.components[ref]
        result = {"ref": ref, "type_hint": comp["attributes"].get("PART_NAME"),
                  "value": comp["attributes"].get("VALUE"), "assembly": comp["assembly"],
                  "pins": _page([self._pin_view(p, detail) for p in comp["pins"].values()], offset, limit)}
        if detail:
            result.update({"primitive": comp["primitive"], "raw_attributes": comp["attributes"],
                           "sections": comp["sections"], "source": comp["source"]})
        return result

    def pin(self, ref, number, *, detail=False):
        if ref not in self.components:
            return {"ref": ref, "pin": str(number), "state": "component_not_declared"}
        pin = self.components[ref]["pins"].get(str(number))
        if pin is None:
            return {"ref": ref, "pin": str(number), "state": "pin_not_declared"}
        return {"ref": ref, **self._pin_view(pin, detail)}

    def net(self, query, *, offset=0, limit=40, detail=False):
        net = self.nets[self._resolve_net(query)]
        rows = [{k: node[k] for k in ("ref", "pin", "name")} for node in net["members"]]
        result = {"name": net["name"], "path": net["path"], "members": _page(rows, offset, limit)}
        if detail:
            result.update({"raw_attributes": net["attributes"], "source": net["source"]})
        return result

    def boundary(self, refs):
        """Complete cut-set for a proposed calculation scope; unknown loads remain.

        This is a membership aid, not an automatic proof of electrical closure.
        The Agent must supply/verify a boundary condition for every outside pin.
        """
        refs = set(refs)
        for ref in refs:
            if ref not in self.components:
                raise KeyError(ref)
        paths = {path for ref in refs for pin in self.components[ref]["pins"].values() for path in pin["nets"]}
        return [{"net": self.nets[path]["name"], "path": path,
                 "outside": [{"ref": n["ref"], "pin": n["pin"], "name": n["name"]}
                             for n in self.nets[path]["members"] if n["ref"] not in refs]}
                for path in self.nets if path in paths]

    def source(self, filename, start_line=1, end_line=None):
        lines = self.raw[filename].splitlines()
        end_line = len(lines) if end_line is None else end_line
        if start_line < 1 or end_line < start_line or end_line > len(lines):
            raise ValueError("invalid source line range")
        return {"file": filename, "start_line": start_line, "end_line": end_line,
                "text": "\n".join(lines[start_line-1:end_line])}
