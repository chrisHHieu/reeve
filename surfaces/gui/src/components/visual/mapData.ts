// The country outlines a map block shades: Natural Earth 1:50m as a TopoJSON in
// longitude and latitude (`worldTopo.json`, built by `scripts/build_world_map.mjs`), decoded the
// first time a map is shown, each country under its ISO alpha-3 code.
//
// What a map opens on is computed here, apart from the map library, so it can be checked without a
// WebGL context: the extent of the countries and points the block carries.

import type { Feature, Geometry, Position } from "geojson";
import type { MapSpec } from "./visualModel";

export type Box = [west: number, south: number, east: number, north: number];

export interface Countries {
  byId: Map<string, Feature<Geometry, { name: string }>>;
}

let loading: Promise<Countries> | null = null;

export function loadCountries(): Promise<Countries> {
  loading ??= Promise.all([import("./worldTopo.json"), import("topojson-client")]).then(([topo, client]) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const topology = (topo.default ?? topo) as any;
    const collection = client.feature(topology, topology.objects.countries) as unknown as {
      features: Feature<Geometry, { name: string }>[];
    };
    const byId = new Map<string, Feature<Geometry, { name: string }>>();
    for (const feature of collection.features) byId.set(String(feature.id), feature);
    return { byId };
  });
  return loading;
}

function positions(geometry: Geometry): Position[] {
  switch (geometry.type) {
    case "Point":
      return [geometry.coordinates];
    case "MultiPoint":
    case "LineString":
      return geometry.coordinates;
    case "Polygon":
    case "MultiLineString":
      return geometry.coordinates.flat();
    case "MultiPolygon":
      return geometry.coordinates.flat(2);
    case "GeometryCollection":
      return geometry.geometries.flatMap(positions);
  }
}

/** The smallest box around some longitudes and latitudes. A set that crosses the antimeridian
 *  (Russia, Fiji, the Aleutians) is measured eastward past 180°, so it is not the whole world. */
export function boxOf(points: Position[]): Box | null {
  if (points.length === 0) return null;
  const measure = (shift: boolean): Box => {
    let box: Box = [Infinity, Infinity, -Infinity, -Infinity];
    for (const [rawLon, lat] of points) {
      const lon = shift && rawLon < 0 ? rawLon + 360 : rawLon;
      box = [Math.min(box[0], lon), Math.min(box[1], lat), Math.max(box[2], lon), Math.max(box[3], lat)];
    }
    return box;
  };
  const plain = measure(false);
  if (plain[2] - plain[0] <= 180) return plain;
  const shifted = measure(true);
  return shifted[2] - shifted[0] < plain[2] - plain[0] ? shifted : plain;
}

export function countryBox(countries: Countries, id: string): Box | null {
  const feature = countries.byId.get(id);
  return feature ? boxOf(positions(feature.geometry)) : null;
}

/** Where a map opens: around everything it carries, or on its one point. */
export type Opening = { box: Box } | { center: [number, number] };

export function openingOf(spec: MapSpec, countries: Countries): Opening | null {
  const all: Position[] = [];
  for (const region of spec.regions) {
    const box = countryBox(countries, region.id);
    if (box) all.push([box[0], box[1]], [box[2], box[3]]);
  }
  for (const point of spec.points) all.push([point.lon, point.lat]);
  if (spec.regions.length === 0 && spec.points.length === 1) return { center: [spec.points[0].lon, spec.points[0].lat] };
  const box = boxOf(all);
  return box ? { box } : null;
}

/** How much of the accent a country's value gets: 18% for the smallest, all of it for the largest. */
export function shareOf(value: number, range: { min: number; max: number } | null): number {
  return range && range.max > range.min ? 0.18 + ((value - range.min) / (range.max - range.min)) * 0.82 : 0.7;
}

/** The regions the outlines have no shape for: listed under the map rather than lost. */
export function unshaped(spec: MapSpec, countries: Countries) {
  return spec.regions.filter((r) => !countries.byId.has(r.id));
}
