"""Native OrCAD PST facts and compact, fully expandable query views.

Supports the PST forms exercised by Clay R6, OpenCellular breakout and J401.
Unresolved library declarations remain local; explicit net endpoints survive.
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


def _property(out, key, value):
    """Preserve differing declarations; never choose the last value silently."""
    if key not in out:
        out[key] = value
    elif out[key] != value:
        values = out[key] if isinstance(out[key], list) else [out[key]]
        for item in value if isinstance(value, list) else [value]:
            if item not in values:
                values.append(item)
        out[key] = values


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
            _property(out, key, value)
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


def _pin_slots(value):
    if value is None:
        raise ValueError("Missing PIN_NUMBER declaration")
    if not isinstance(value, str):
        raise ValueError("Conflicting PIN_NUMBER declarations")
    match = re.fullmatch(r"\(\s*([A-Za-z0-9_]+(?:\s*,\s*[A-Za-z0-9_]+)*)\s*\)", value)
    if not match:
        raise ValueError(f"Unresolved PIN_NUMBER {value!r}")
    numbers = [x.strip() for x in match.group(1).split(",")]
    # In the observed heterogeneous-section exports, zero is an empty slot.
    # A singleton (0) is retained as a literal physical pad, not guessed away.
    return [(str(i + 1), n) for i, n in enumerate(numbers)
            if len(numbers) == 1 or n != "0"]


def _nc_pins(value):
    if not isinstance(value, str):
        raise ValueError("Unresolved NC_PINS declarations")
    match = re.fullmatch(r"\(\s*([A-Za-z0-9_]+(?:\s*,\s*[A-Za-z0-9_]+)*)\s*\)", value)
    if not match:
        raise ValueError(f"Unresolved NC_PINS {value!r}")
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
                "sources": [source] if source else [], "raw_attributes": {},
                "sections": [], "mapping_issues": []}

    def _parse_chips(self):
        r = Reader(self.raw["pstchip.dat"], "pstchip.dat")
        r.header("LIBRARY_PARTS")
        while r.peek("primitive"):
            start = r.take().line
            name = r.take(quoted=True).value
            r.take(";")
            r.take("pin")
            pins = {}
            unresolved = []
            while not r.peek("end_pin"):
                name_token = r.take(quoted=True)
                r.take(":")
                # Each pin has one or more semicolon-terminated properties;
                # the next quoted token starts a new pin.
                attrs = {}
                while r.peek() and not r.peek().quoted and not r.peek("end_pin"):
                    for key, value in r.properties().items():
                        _property(attrs, key, value)
                location = r.location(name_token.line)
                try:
                    slots = _pin_slots(attrs.get("PIN_NUMBER"))
                except ValueError as exc:
                    unresolved.append({"name": name_token.value, "attributes": attrs,
                                       "source": location, "reason": str(exc)})
                    continue
                for section, number in slots:
                    if number not in pins:
                        pins[number] = self._new_pin(number, declared=True)
                    pin = pins[number]
                    if name_token.value not in pin["names"]:
                        pin["names"].append(name_token.value)
                    if section not in pin["sections"]:
                        pin["sections"].append(section)
                    pin["sources"].append(location)
                    for key, value in attrs.items():
                        _property(pin["raw_attributes"], key, value)
            r.take("end_pin")
            r.take(";")
            r.take("body")
            attrs = r.properties("end_body")
            r.take("end_body")
            r.take(";")
            r.take("end_primitive")
            r.take(";")
            if "NC_PINS" in attrs:
                try:
                    values = attrs["NC_PINS"] if isinstance(attrs["NC_PINS"], list) else [attrs["NC_PINS"]]
                    for value in values:
                        for number in _nc_pins(value):
                            if number not in pins:
                                pins[number] = self._new_pin(number, declared=True, source=r.location(start))
                            pins[number]["nc_declared"] = True
                except ValueError as exc:
                    unresolved.append({"name": "NC_PINS", "attributes": attrs["NC_PINS"],
                                       "source": r.location(start), "reason": str(exc)})
            if "POWER_PINS" in attrs:
                values = attrs["POWER_PINS"] if isinstance(attrs["POWER_PINS"], list) else [attrs["POWER_PINS"]]
                for value in values:
                    match = re.fullmatch(r"\(([^()]*)\)", value)
                    groups = match.group(1).split(";") if match else []
                    for group in groups:
                        power = re.fullmatch(r"\s*([^:;]+):\s*([A-Za-z0-9_]+(?:\s*,\s*[A-Za-z0-9_]+)*)\s*", group)
                        if not power:
                            break
                        label, numbers = power.groups()
                        for number in numbers.split(","):
                            number = number.strip()
                            if number not in pins:
                                pins[number] = self._new_pin(number, declared=True, source=r.location(start))
                            pin = pins[number]
                            if label.strip() not in pin["names"]:
                                pin["names"].append(label.strip())
                            pin.setdefault("power_declarations", []).append(label.strip())
                    else:
                        if groups:
                            continue
                    unresolved.append({"name": "POWER_PINS", "attributes": value,
                                       "source": r.location(start), "reason": "Unresolved POWER_PINS expression"})
            if name in self.primitives:
                raise DatError(f"Duplicate primitive {name}")
            self.primitives[name] = {"pins": pins, "attributes": attrs,
                                    "unresolved_declarations": unresolved, "source": r.location(start)}
        r.finish()

    def _parse_parts(self):
        r = Reader(self.raw["pstxprt.dat"], "pstxprt.dat")
        r.header("EXPANDEDPARTLIST")
        while r.peek("PART_NAME"):
            start = r.take().line
            ref = r.take(quoted=False).value
            primitive = r.take(quoted=True).value
            r.take(":")
            instance_attrs = r.properties() if not r.peek(";") else {}
            if r.peek(";"):
                r.take(";")
            sections = []
            while r.peek("SECTION_NUMBER"):
                r.take()
                number = r.take(quoted=False).value
                path = r.take(quoted=True).value
                r.take(":")
                sections.append({"number": number, "path": path, "attributes": r.properties()})
            if any(not s["number"].isdigit() or int(s["number"]) < 1 for s in sections):
                raise DatError(f"{ref}: invalid section index")
            if len({s["number"] for s in sections}) != len(sections):
                raise DatError(f"{ref}: conflicting section indices")
            if ref in self.components:
                raise DatError(f"Duplicate physical instance {ref}")
            library = self.primitives.get(primitive)
            if library is None:
                self.diagnostics.append({"kind": "undeclared_primitive", "ref": ref})
            unresolved = deepcopy(library["unresolved_declarations"]) if library else []
            for item in unresolved:
                self.diagnostics.append({"kind": "unresolved_library_declaration", "ref": ref,
                                         "name": item["name"], "reason": item["reason"]})
            attributes = deepcopy(library["attributes"]) if library else {}
            # Instance properties have occurrence scope; retain the underlying
            # primitive separately for detail queries.
            attributes.update(instance_attrs)
            self.components[ref] = {
                "ref": ref, "primitive": primitive,
                "attributes": attributes, "instance_attributes": instance_attrs,
                "pins": deepcopy(library["pins"]) if library else {},
                "sections": sections, "source": r.location(start),
                "unresolved_declarations": unresolved,
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
            is_nc = name == "NC" and path == "NC"
            ambiguous_nc = is_nc and attrs.get("C_SIGNAL") != "NC"
            if path in self.nets:
                raise DatError(f"Duplicate full net path {path}")
            net = {"name": name, "path": path, "attributes": attrs, "members": []}
            seen_members = {}
            while r.peek("NODE_NAME"):
                node_start = r.take().line
                ref = r.take(quoted=False).value
                number = r.take(quoted=False).value
                node_path = r.take(quoted=True).value
                r.take(":")
                pin_name = r.take(quoted=True).value
                r.take(":")
                r.take(";")
                if ref not in self.components:
                    self.components[ref] = {"ref": ref, "primitive": None, "attributes": {},
                        "pins": {}, "sections": [], "source": r.location(node_start),
                        "assembly": "unspecified", "unresolved_declarations": [], "instance_attributes": {}}
                    self.diagnostics.append({"kind": "undeclared_component", "ref": ref})
                comp = self.components[ref]
                if number not in comp["pins"]:
                    comp["pins"][number] = self._new_pin(number)
                    self.diagnostics.append({"kind": "undeclared_pin", "ref": ref, "pin": number})
                pin = comp["pins"][number]
                if ambiguous_nc:
                    pin["mapping_issues"].append("ambiguous_nc_group")
                matches = [s["number"] for s in comp["sections"] if s["path"] == node_path]
                if comp["sections"] and not matches:
                    pin["mapping_issues"].append("occurrence_path_mismatch")
                elif matches and pin["sections"] and not set(matches).intersection(pin["sections"]):
                    pin["mapping_issues"].append("physical_pin_section_mismatch")
                for unresolved in comp["unresolved_declarations"]:
                    if (unresolved["name"] == pin_name
                            and isinstance(unresolved.get("attributes"), dict)
                            and isinstance(unresolved["attributes"].get("PIN_NUMBER"), list)):
                        pin["mapping_issues"].append("conflicting_library_pin_numbers")
                if pin_name not in pin["names"]:
                    pin["names"].append(pin_name)
                if is_nc:
                    pin["nc_exported"] = True
                elif path not in pin["nets"]:
                    pin["nets"].append(path)
                pin["sources"].append(r.location(node_start))
                member = {"ref": ref, "pin": number, "name": pin_name,
                          "path": node_path, "source": r.location(node_start)}
                if (ref, number) in seen_members:
                    previous = seen_members[(ref, number)]
                    previous.setdefault("additional_records", []).append(member)
                    self.diagnostics.append({"kind": "duplicate_endpoint_record", "ref": ref,
                                             "pin": number, "net_path": path})
                else:
                    seen_members[(ref, number)] = member
                    net["members"].append(member)
            net["source"] = r.location(start)
            if is_nc:
                self.nc_records.append(net)
            else:
                self.nets[path] = net
                signal_aliases = attrs.get("C_SIGNAL", path)
                if not isinstance(signal_aliases, list):
                    signal_aliases = [signal_aliases]
                for alias in {name, path, *signal_aliases}:
                    self._aliases[alias].append(path)
        r.finish()
        for comp in self.components.values():
            for pin in comp["pins"].values():
                if self._pin_status(pin) == "conflicting_records":
                    self.diagnostics.append({"kind": "conflicting_pin_records", "ref": comp["ref"], "pin": pin["number"]})

    @staticmethod
    def _pin_status(pin):
        if (pin["mapping_issues"] or len(pin["nets"]) > 1
                or (pin["nets"] and (pin["nc_declared"] or pin["nc_exported"]))):
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
        singletons = [self.nets[p]["name"] for p in pin["nets"] if len(self.nets[p]["members"]) == 1]
        if singletons:
            result["singleton_nets"] = singletons
        if pin["mapping_issues"]:
            result["mapping_issues"] = sorted(set(pin["mapping_issues"]))
        if any(len(self._aliases[self.nets[p]["name"]]) > 1 for p in pin["nets"]):
            result["net_paths"] = pin["nets"]
        if detail:
            result.update({"net_paths": pin["nets"], "nc_declared": pin["nc_declared"],
                "nc_exported": pin["nc_exported"], "raw_attributes": pin["raw_attributes"],
                "sources": pin["sources"], "sections": pin["sections"]})
            if "power_declarations" in pin:
                result["power_declarations"] = pin["power_declarations"]
        return result

    def overview(self):
        pins = [p for c in self.components.values() for p in c["pins"].values()]
        result = {"components": len(self.components), "physical_pins": len(pins),
                "electrical_nets": len(self.nets),
                "connected_endpoints": sum(len(n["members"]) for n in self.nets.values()),
                "pin_states": dict(Counter(self._pin_status(p) for p in pins)),
                "nc_exported_endpoints": sum(len(n["members"]) for n in self.nc_records),
                "nc_declared_pins": sum(p["nc_declared"] for p in pins),
                "input_diagnostic_count": len(self.diagnostics),
                "next": "inventory enumerates every instance; component/net expand all members"}
        unresolved = sum(len(c["unresolved_declarations"]) for c in self.components.values())
        if unresolved or any(c["primitive"] not in self.primitives for c in self.components.values()):
            result.update(pin_inventory_complete=False, unresolved_library_declarations=unresolved)
        return result

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
        if comp["unresolved_declarations"]:
            result["unresolved_declaration_count"] = len(comp["unresolved_declarations"])
        if detail:
            result.update({"primitive": comp["primitive"], "raw_attributes": comp["attributes"],
                           "sections": comp["sections"], "source": comp["source"],
                           "unresolved_declarations": comp["unresolved_declarations"],
                           "instance_attributes": comp["instance_attributes"]})
        return result

    def pin(self, ref, number, *, detail=False):
        if ref not in self.components:
            return {"ref": ref, "pin": str(number), "state": "component_not_declared"}
        pin = self.components[ref]["pins"].get(str(number))
        if pin is None:
            return {"ref": ref, "pin": str(number), "state": "pin_not_declared"}
        return {"ref": ref, **self._pin_view(pin, detail)}

    def _member_view(self, node):
        result = {k: node[k] for k in ("ref", "pin", "name")}
        pin = self.components[node["ref"]]["pins"][node["pin"]]
        state = self._pin_status(pin)
        if state != "connected":
            result["state"] = state
        if not pin["declared"]:
            result["declaration"] = "absent"
        if pin["mapping_issues"]:
            result["mapping_issues"] = sorted(set(pin["mapping_issues"]))
        return result

    def net(self, query, *, offset=0, limit=40, detail=False):
        net = self.nets[self._resolve_net(query)]
        rows = [self._member_view(node) for node in net["members"]]
        result = {"name": net["name"], "path": net["path"], "members": _page(rows, offset, limit)}
        if detail:
            result.update({"raw_attributes": net["attributes"], "source": net["source"]})
        return result

    def boundary(self, refs):
        """Complete cut-set for a proposed calculation scope; unknown loads remain.

        This is a membership aid, not an automatic proof of electrical closure.
        The Agent selects dependencies relevant to the current assertion;
        external effects may be bounded or grouped when that is justified.
        Enumeration alone does not establish those electrical conditions.
        """
        refs = set(refs)
        for ref in refs:
            if ref not in self.components:
                raise KeyError(ref)
        paths = {path for ref in refs for pin in self.components[ref]["pins"].values() for path in pin["nets"]}
        return [{"net": self.nets[path]["name"], "path": path,
                 "outside": [self._member_view(n)
                             for n in self.nets[path]["members"] if n["ref"] not in refs]}
                for path in self.nets if path in paths]

    def source(self, filename, start_line=1, end_line=None):
        lines = self.raw[filename].splitlines()
        end_line = len(lines) if end_line is None else end_line
        if start_line < 1 or end_line < start_line or end_line > len(lines):
            raise ValueError("invalid source line range")
        return {"file": filename, "start_line": start_line, "end_line": end_line,
                "text": "\n".join(lines[start_line-1:end_line])}
