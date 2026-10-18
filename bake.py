"""World Heritage sites from open data only, as GeoParquet and vector tiles. Two sources, two licences, never merged:
  sites.parquet     CC0 (Wikidata): one row per Wikidata item with a World Heritage Site ID (P757), component parts included
                    (IDs like 600-001). geometry = its coordinate (P625), null when Wikidata has none
  sites.pmtiles     layer `sites`, every site that has a coordinate, zooms 0 to 10
  areas.parquet     ODbL (OpenStreetMap): every way and relation tagged heritage:operator=whc, as mapped by OpenStreetMap
                    contributors (not UNESCO's official boundaries). Polygons where the members close into rings, else lines
  areas.pmtiles     layer `areas`, every outline, zooms 0 to 14
index.json counts the rows of each file, and the features at the deepest zoom of each tile set.
UNESCO's own lists, KML, XLS and ArcGIS services are never fetched: their terms forbid redistribution without permission.
Usage: python3 bake.py"""
import datetime, json, os, shutil, subprocess, time, urllib.error, urllib.parse, urllib.request
import duckdb
import shapely
from shapely.geometry import LineString, MultiLineString, MultiPoint, Point, Polygon, mapping
from shapely.ops import polygonize, unary_union
import sources

UA = {'User-Agent': 'AlreadyOpen world-heritage (https://github.com/AlreadyOpen/world-heritage)'}
SPARQL = 'https://query.wikidata.org/sparql'
SITES_Q = '''SELECT ?item ?whc ?coord ?en ?fr ?iso3 ?year WHERE { ?item wdt:P757 ?whc . OPTIONAL{?item wdt:P625 ?coord}
 OPTIONAL{?item rdfs:label ?en FILTER(LANG(?en)="en")} OPTIONAL{?item rdfs:label ?fr FILTER(LANG(?fr)="fr")}
 OPTIONAL{?item wdt:P17/wdt:P298 ?iso3}
 OPTIONAL{?item p:P1435 ?st . ?st ps:P1435 wd:Q9259 ; pq:P580 ?t . BIND(YEAR(?t) AS ?year)} }'''
CRIT_Q = 'SELECT ?item ?c WHERE { ?item wdt:P757 [] ; wdt:P2614 ?k . ?k rdfs:label ?c FILTER(LANG(?c)="en") }'
# overpass-api.de answered the global tag query in 65 s on 2026-10-18 but a global `out geom` failed everywhere (500/504),
# so the bake lists ids first, then fetches geometry in batches. Servers are tried in order.
OVERPASS = ['https://overpass-api.de/api/interpreter', 'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
            'https://overpass.kumi.systems/api/interpreter', 'https://overpass.private.coffee/api/interpreter']
BATCH = 100
KEEP = ['name', 'name:en', 'name:fr', 'ref:whc', 'whc:criteria', 'whc:inscription_date', 'protect_class', 'protection_title',
        'wikidata', 'wikipedia', 'boundary', 'heritage', 'heritage:operator', 'type', 'website']
SITES_MAXZOOM, AREAS_MAXZOOM = 10, 14
LINEAR = {'route', 'superroute', 'network', 'waterway'}   # relation types that are lines, never areas
CLAMP = 85.0511                                                       # Web Mercator stops here (as in world-airports)


def fetch(url, data, accept='application/json', tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), headers={**UA, 'Accept': accept})
            with urllib.request.urlopen(req, timeout=900) as r:
                return json.loads(r.read())
        except Exception as e:                                         # HTML error pages fail json.loads too
            print(f'{url}: {e}', flush=True)
            if isinstance(e, urllib.error.URLError) and not isinstance(e, urllib.error.HTTPError):
                raise ConnectionError(f'{url}: unreachable') from e    # no route (GitHub runners to overpass-api.de): don't wait on it
            if i < tries - 1: time.sleep(30 * (i + 1))
    raise RuntimeError(f'{url}: no JSON after {tries} tries')


DEAD = set()                                                          # servers unreachable from here, skipped for the rest of the run


def overpass(q):
    for s in [s for s in OVERPASS if s not in DEAD]:
        try: return fetch(s, {'data': f'[out:json][timeout:900];{q}'}, tries=2)['elements']   # an error page has no 'elements'
        except ConnectionError as e: DEAD.add(s); print(f'overpass: {e}, skipped from now on', flush=True)
        except Exception as e: print(f'overpass: {e}', flush=True)
    raise RuntimeError('every Overpass server failed')


def cached(name, get):
    """fresh every run; the cache copy is what the rest of the bake reads (and what a failed later step can rerun from)"""
    p = os.path.join(sources.CACHE, name)
    if os.environ.get('REUSE_CACHE') == '1' and os.path.exists(p): return json.load(open(p))   # local reruns of the processing only
    d = get(); json.dump(d, open(p + '.part', 'w')); os.replace(p + '.part', p)
    return d


def sites():
    rows = cached('wikidata-sites.json', lambda: fetch(SPARQL, {'query': SITES_Q}, 'application/sparql-results+json'))['results']['bindings']
    crit = cached('wikidata-criteria.json', lambda: fetch(SPARQL, {'query': CRIT_Q}, 'application/sparql-results+json'))['results']['bindings']
    by, val = {}, lambda r, k: r[k]['value'] if k in r else None
    for r in rows:                                                     # several rows per item (countries, labels, coordinates)
        q = val(r, 'item').rsplit('/', 1)[1]
        s = by.setdefault(q, {'id': q, 'whc': set(), 'coords': set(), 'en': None, 'fr': None, 'iso3': set(), 'year': set(), 'criteria': set()})
        s['whc'].add(val(r, 'whc'))
        if c := val(r, 'coord'):
            if c.startswith('Point('): s['coords'].add(tuple(map(float, c[6:-1].split())))
        for k in ['en', 'fr']: s[k] = s[k] or val(r, k)
        if v := val(r, 'iso3'): s['iso3'].add(v)
        if v := val(r, 'year'): s['year'].add(int(float(v)))
    for r in crit:
        q = val(r, 'item').rsplit('/', 1)[1]
        if q in by: by[q]['criteria'].add(val(r, 'c'))
    roman = ['(i)', '(ii)', '(iii)', '(iv)', '(v)', '(vi)', '(vii)', '(viii)', '(ix)', '(x)']
    out = []
    for s in by.values():
        c = sorted(s['coords'])
        out.append({'id': s['id'], 'whc': ';'.join(sorted(s['whc'])), 'component': all('-' in w for w in s['whc']),
                    'name_en': s['en'], 'name_fr': s['fr'], 'countries': ';'.join(sorted(s['iso3'])) or None,
                    'inscribed': min(s['year']) if s['year'] else None,
                    'criteria': ''.join(sorted(s['criteria'], key=lambda x: roman.index(x) if x in roman else 99)) or None,
                    'wikidata': f"https://www.wikidata.org/wiki/{s['id']}", 'coordinates': len(c),
                    'lon': c[0][0] if c else None, 'lat': c[0][1] if c else None})
    return sorted(out, key=lambda s: int(s['id'][1:]))


def geometry(e, by, seen=()):
    """shapely geometry of one Overpass element (`out geom`): ways close into a polygon, relations polygonize their member
    ways (outer minus inner) and take in member relations that are in `by` (site relations often hold multipolygons);
    anything that does not close stays as lines, member nodes as points, so nothing is dropped"""
    line = lambda g: [(p['lon'], p['lat']) for p in g if p]
    if e['type'] == 'way':
        c = line(e.get('geometry', []))
        if len(c) >= 4 and c[0] == c[-1] and e.get('tags', {}).get('area') != 'no': return shapely.make_valid(Polygon(c))
        return LineString(c) if len(c) >= 2 else None
    ms = e.get('members', [])
    ways = [(m.get('role', ''), line(m['geometry'])) for m in ms if m['type'] == 'way' and m.get('geometry')]
    ways = [(r, c) for r, c in ways if len(c) >= 2]
    nodes = [Point(m['lon'], m['lat']) for m in ms if m['type'] == 'node' and 'lon' in m]
    subs = [geometry(by[('relation', m['ref'])], by, (*seen, e['id'])) for m in ms
            if m['type'] == 'relation' and ('relation', m['ref']) in by and m['ref'] not in seen and m['ref'] != e['id']]
    # ponytail: outer minus inner by role; an island in a lake in an outer ring comes out filled. Fine for heritage outlines.
    ring = lambda ls: unary_union([shapely.make_valid(p) for p in polygonize(unary_union([LineString(c) for c in ls]))]) if ls else None
    # a route, network or waterway is lines even where its ways loop (polygonizing the Way of St James filled half of France)
    linear = e.get('tags', {}).get('type') in LINEAR
    outer, inner = (None, None) if linear else (ring([c for r, c in ways if r != 'inner']), ring([c for r, c in ways if r == 'inner']))
    own = None
    if outer is not None and not outer.is_empty:
        own = outer.difference(inner) if inner is not None and not inner.is_empty else outer
    if own is None or own.is_empty:                                    # nothing closes: the member ways as lines
        own = MultiLineString([c for _, c in ways]) if ways else None
    parts = [g for g in [own, *subs] if g is not None and not g.is_empty]
    if not parts: return MultiPoint(nodes) if nodes else None
    # one geometry kind per feature (tiles and GeoParquet readers handle that best): areas if there are any, else lines
    polys = [p for g in parts for p in getattr(g, 'geoms', [g]) if p.geom_type in ('Polygon', 'MultiPolygon')]
    if polys and not linear: return unary_union(polys)
    return unary_union([p for g in parts for p in getattr(g, 'geoms', [g]) if 'LineString' in p.geom_type])


def one_kind(g):
    """make_valid can return a GeometryCollection (a polygon plus a stray line): keep its areas, else its lines"""
    if g is None or g.geom_type != 'GeometryCollection': return g
    for k in ['Polygon', 'LineString', 'Point']:
        if xs := [p for p in g.geoms if k in p.geom_type]: return unary_union(xs)
    return None


def geom_batch(t, ids, tmp, strict=True):
    """`out tags geom` for these ids; kept in tmp (inside the CI cache) so a rerun after a failure or timeout resumes (tmp is removed when the bake ends).
    A batch every server refuses is split in two, down to single elements (the servers 502/504 under load)"""
    p = os.path.join(tmp, f'osm-{t}-{ids[0]}-{len(ids)}.json')
    if os.path.exists(p): return json.load(open(p))
    try:
        els = overpass(f'{t}(id:{",".join(map(str, ids))});out geom;')
        if len(els) != len(ids): raise RuntimeError(f'{len(els)} of {len(ids)} {t}s returned')   # a timeout answers 200, short
    except RuntimeError:
        if len(ids) == 1:
            if strict: raise
            print(f'osm: {t} {ids[0]} not fetched (deleted, or every server failed)', flush=True); return []
        els = geom_batch(t, ids[:len(ids) // 2], tmp, strict) + geom_batch(t, ids[len(ids) // 2:], tmp, strict)
    json.dump(els, open(p, 'w'))
    return els


def subrelations(els, tmp, depth=3):
    """member relations that are not themselves tagged (a site relation often holds untagged multipolygons), three levels
    deep: geometry only, never features of their own"""
    have, extra = {(e['type'], e['id']) for e in els}, []
    for _ in range(depth):
        refs = sorted({m['ref'] for e in els + extra if e['type'] == 'relation' for m in e.get('members', [])
                       if m['type'] == 'relation' and ('relation', m['ref']) not in have})
        for i in range(0, len(refs), BATCH): extra += geom_batch('relation', refs[i:i + BATCH], tmp, strict=False)
        have |= {('relation', r) for r in refs}
    return extra


def areas(tmp):
    def get():
        ids = overpass('(way["heritage:operator"="whc"];relation["heritage:operator"="whc"];);out ids;')
        els = []
        for t in ['way', 'relation']:
            xs = sorted(x['id'] for x in ids if x['type'] == t)
            for i in range(0, len(xs), BATCH):
                els += geom_batch(t, xs[i:i + BATCH], tmp)
                print(f'osm {t}s {min(i + BATCH, len(xs))}/{len(xs)}', flush=True)
        assert len(els) == len(ids), f'Overpass: {len(ids)} ids listed, {len(els)} with geometry'
        return {'elements': els, 'members': subrelations(els, tmp)}
    d, out = cached('osm.json', get), []
    els, by = d['elements'], {(e['type'], e['id']): e for e in d['elements'] + d['members']}
    for e in els:
        g = one_kind(geometry(e, by))
        out.append({'id': f"{e['type']}/{e['id']}", 'osm': f"https://www.openstreetmap.org/{e['type']}/{e['id']}",
                    **{k.replace(':', '_'): e.get('tags', {}).get(k) for k in KEEP}, 'geom': g})
    return out


def write_geojsonl(feats, out):
    with open(out, 'w') as f:
        for p, g in feats: f.write(json.dumps({'type': 'Feature', 'geometry': g, 'properties': {k: v for k, v in p.items() if v is not None}}) + '\n')


def tiles(geojsonl, out, layer, maxzoom, extra=()):
    subprocess.run(['tippecanoe', '-o', out, '--force', '-l', layer, '-Z0', f'-z{maxzoom}', '-r1', '--no-feature-limit',
                    '--no-tile-size-limit', '--quiet', *extra, geojsonl], check=True)
    # every feature must be in the deepest zoom: count distinct ids there (a feature crossing tiles shows in each)
    ids = set()
    with subprocess.Popen(['tippecanoe-decode', '-z', str(maxzoom), '-Z', str(maxzoom), out], stdout=subprocess.PIPE, text=True) as p:
        for line in p.stdout:
            line = line.strip().rstrip(',')
            if line.startswith('{ "type": "Feature"'): ids.add(json.loads(line)['properties']['id'])
    return len(ids)


def main():
    os.makedirs(sources.CACHE, exist_ok=True); os.makedirs(sources.OUT, exist_ok=True)
    o = lambda f: os.path.join(sources.OUT, f)
    tmp = os.path.join(sources.CACHE, 'tmp'); os.makedirs(tmp, exist_ok=True)     # in the CI cache: a run that times out resumes
    con = duckdb.connect(); con.execute('INSTALL spatial; LOAD spatial')

    S = sites(); counts = {'sites': len(S), 'sites_located': sum(s['lon'] is not None for s in S),
                           'sites_components': sum(s['component'] for s in S)}
    json.dump(S, open(os.path.join(tmp, 'sites.json'), 'w'))
    con.execute(f"""COPY (SELECT * EXCLUDE (lon, lat), CASE WHEN lon IS NOT NULL THEN ST_Point(lon, lat) END AS geometry
        FROM read_json('{os.path.join(tmp, 'sites.json')}', format='array')) TO '{o('sites.parquet')}' (FORMAT parquet, COMPRESSION zstd)""")
    write_geojsonl([({k: v for k, v in s.items() if k not in ('lon', 'lat')},
                     {'type': 'Point', 'coordinates': [s['lon'], max(min(s['lat'], CLAMP), -CLAMP)]}) for s in S if s['lon'] is not None],
                   os.path.join(tmp, 'sites.geojsonl'))
    counts['sites_tiles_deepest_zoom'] = tiles(os.path.join(tmp, 'sites.geojsonl'), o('sites.pmtiles'), 'sites', SITES_MAXZOOM)

    A = areas(tmp)
    # Helen, 2026-10-19: a route or network only with a World Heritage ID (ref:whc). OSM tags whole networks (the Way of St
    # James across France) where UNESCO lists sections; waterways stay as they are.
    route = lambda a: a['type'] in ('route', 'superroute', 'network') and not a['ref_whc']
    kept = [a for a in A if a['geom'] is not None and not route(a)]
    counts['areas'] = len(kept); counts['areas_osm_elements'] = len(A); counts['areas_routes_without_whc_id_dropped'] = sum(map(route, A))
    counts['areas_polygons'] = sum(a['geom'].geom_type in ('Polygon', 'MultiPolygon') for a in kept)
    with open(os.path.join(tmp, 'areas.geojsonl'), 'w') as f:
        for a in kept:
            p = {k: v for k, v in a.items() if k != 'geom' and v is not None}
            f.write(json.dumps({'type': 'Feature', 'geometry': mapping(a['geom']), 'properties': p}) + '\n')
    # GeoParquet from the same lines (duckdb reads GeoJSON geometry with ST_GeomFromGeoJSON); every OSM tag kept is a column
    cols = ['id', 'osm'] + [k.replace(':', '_') for k in KEEP]
    con.execute(f"""COPY (SELECT {', '.join(f"properties->>'{c}' AS \"{c}\"" for c in cols)},
        ST_GeomFromGeoJSON(geometry) AS geometry FROM read_json('{os.path.join(tmp, 'areas.geojsonl')}', format='newline_delimited',
        columns={{'properties': 'JSON', 'geometry': 'JSON'}})) TO '{o('areas.parquet')}' (FORMAT parquet, COMPRESSION zstd)""")
    counts['areas_tiles_deepest_zoom'] = tiles(os.path.join(tmp, 'areas.geojsonl'), o('areas.pmtiles'), 'areas', AREAS_MAXZOOM,
                                               ['--no-tiny-polygon-reduction'])
    shutil.rmtree(tmp)
    for k, n in [('sites', counts['sites_located']), ('areas', counts['areas'])]:
        assert counts[k + '_tiles_deepest_zoom'] == n, f'{k}.pmtiles: {counts[k + "_tiles_deepest_zoom"]} features at the deepest zoom, {n} rows'
    for k, n in [('sites', counts['sites']), ('areas', counts['areas'])]:
        assert con.execute(f"SELECT count(*) FROM '{o(k + '.parquet')}'").fetchone()[0] == n
    idx = {'built': datetime.datetime.now().isoformat(timespec='seconds'), 'counts': counts,
           'licences': {'sites.parquet': 'CC0-1.0 (Wikidata)', 'sites.pmtiles': 'CC0-1.0 (Wikidata)',
                        'areas.parquet': 'ODbL-1.0 (© OpenStreetMap contributors)', 'areas.pmtiles': 'ODbL-1.0 (© OpenStreetMap contributors)'}}
    json.dump(idx, open(o('index.json'), 'w'), indent=1)
    print(json.dumps(idx))


if __name__ == '__main__':
    main()
