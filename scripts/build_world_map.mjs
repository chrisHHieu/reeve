// Builds the two data files the `map` visual block reads, from published packages:
//
//   surfaces/gui/src/components/visual/worldTopo.json  country outlines (TopoJSON, lon/lat)
//   harness/tools/visual/countries.json                 the names a model may give each country
//
// Sources (fetch them with `npm pack world-atlas@2.0.2 i18n-iso-countries@7.14.0` into an empty
// folder and unpack each .tgz into its own folder):
//   world-atlas 2.0.2 (ISC), Natural Earth 1:50m admin-0 boundaries (public domain)
//   i18n-iso-countries 7.14.0 (MIT), ISO 3166 codes and country names in English and Vietnamese
//
// Run: node scripts/build_world_map.mjs <folder with the unpacked packages>
//
// The outlines stay a quantized topology in longitude and latitude: the GUI draws them on a map it
// can zoom and pan (MapLibre), over the online basemap or alone when the basemap is off, so they
// are 1:50m (Singapore and Hong Kong have a shape) and each country carries its ISO alpha-3 code
// and English name. Antarctica is left out: no figure is ever about it.

import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const from = process.argv[2];
if (!from) throw new Error("usage: node scripts/build_world_map.mjs <folder with the unpacked packages>");
const atlas = JSON.parse(readFileSync(join(from, "world-atlas-2.0.2/package/countries-50m.json"), "utf8"));
const iso = join(from, "i18n-iso-countries-7.14.0/package");
const codes = JSON.parse(readFileSync(join(iso, "codes.json"), "utf8"));
const names = (lang) => JSON.parse(readFileSync(join(iso, `langs/${lang}.json`), "utf8")).countries;
const en = names("en");
const vi = names("vi");

const byNumeric = new Map(codes.map(([a2, a3, numeric]) => [String(numeric).padStart(3, "0"), { a2, a3 }]));
const list = (value) => (Array.isArray(value) ? value : value ? [value] : []);
const byName = new Map(codes.flatMap(([a2, a3]) => list(en[a2]).map((name) => [name, { a2, a3 }])));

const geometries = [];
const drawn = new Set();
for (const geometry of atlas.objects.countries.geometries) {
  // A few outlines have no ISO numeric code in the atlas (Kosovo, N. Cyprus, Somaliland): found by
  // their English name when ISO has one, else kept under their own name.
  const code = geometry.id
    ? byNumeric.get(String(geometry.id).padStart(3, "0"))
    : byName.get(geometry.properties.name);
  if (code?.a3 === "ATA" || !geometry.type) continue;
  const id = code?.a3 ?? `_${geometry.properties.name}`;
  geometries.push({ ...geometry, id, properties: { name: geometry.properties.name } });
  drawn.add(id);
}
const topology = { ...atlas, objects: { countries: { type: "GeometryCollection", geometries } } };
delete topology.objects.land;

writeFileSync(new URL("../surfaces/gui/src/components/visual/worldTopo.json", import.meta.url), JSON.stringify(topology));

const countries = codes
  .map(([a2, a3]) => ({
    a3,
    a2,
    names: [...new Set([...list(en[a2]), ...list(vi[a2])])],
    drawn: drawn.has(a3),
  }))
  .filter((c) => c.names.length);
writeFileSync(
  new URL("../harness/tools/visual/countries.json", import.meta.url),
  JSON.stringify(countries, null, 0).replace(/\},\{/g, "},\n{") + "\n",
);
console.log(`${geometries.length} outlines, ${countries.length} countries`);
