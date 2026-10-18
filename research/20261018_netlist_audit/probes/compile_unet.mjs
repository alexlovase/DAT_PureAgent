import {readdirSync,readFileSync,writeFileSync} from "node:fs";
import {join} from "node:path";
import {fileURLToPath} from "node:url";
import {stripTypeScriptTypes} from "node:module";
let count=0;
function build(dir){for(const e of readdirSync(dir,{withFileTypes:true})){const p=join(dir,e.name);if(e.isDirectory())build(p);else if(p.endsWith(".ts")){const s=readFileSync(p,"utf8");writeFileSync(p.slice(0,-3)+".js",stripTypeScriptTypes(s,{mode:"strip",sourceUrl:p}));count++;}}}
build(fileURLToPath(new URL("../_vendor/unet/src",import.meta.url)));
console.log(JSON.stringify({compiled_production_modules:count,method:"Node built-in stripTypeScriptTypes; no runtime edits"}));

