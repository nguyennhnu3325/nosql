"""Statistics for the directed ROAD graph, independent of Neo4j/GDS."""
import math
from collections import Counter


def valid_distance(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def distribution(values, bounds):
    counts = [0] * (len(bounds) + 1)
    for value in values:
        counts[next((i for i, bound in enumerate(bounds) if value < bound), len(bounds))] += 1
    labels = [f'< {bounds[0]} m'] + [f'{a}–< {b} m' for a, b in zip(bounds, bounds[1:])] + [f'≥ {bounds[-1]} m']
    return [dict(type=label, count=count) for label, count in zip(labels, counts)]


def _base_analysis(data):
    ids = {str(j['junctionId']) for j in data['junctions']}
    outgoing = {j: set() for j in ids}
    incoming = {j: set() for j in ids}
    lengths, by_type = [], Counter()
    for road in data['roads']:
        source, target = str(road['source']), str(road['target'])
        if source in ids and target in ids:
            outgoing[source].add(target)
            incoming[target].add(source)
        props = road['properties']
        length = props.get('lengthM')
        if valid_distance(length):
            lengths.append(length)
            by_type[str(props.get('highway') or 'Chưa phân loại')] += length / 1000

    # Iterative Kosaraju avoids recursion limits on large road networks.
    visited, order = set(), []
    for root in ids:
        if root in visited:
            continue
        visited.add(root)
        stack = [(root, iter(outgoing[root]))]
        while stack:
            node, neighbours = stack[-1]
            child = next(neighbours, None)
            if child is None:
                order.append(node)
                stack.pop()
            elif child not in visited:
                visited.add(child)
                stack.append((child, iter(outgoing[child])))

    def components(roots, neighbours):
        seen, sizes = set(), []
        for root in roots:
            if root in seen:
                continue
            seen.add(root)
            stack, size = [root], 0
            while stack:
                node = stack.pop()
                size += 1
                for child in neighbours(node):
                    if child not in seen:
                        seen.add(child)
                        stack.append(child)
            sizes.append(size)
        return sorted(sizes, reverse=True)

    weak = components(ids, lambda j: outgoing[j] | incoming[j])
    strong = components(reversed(order), lambda j: incoming[j])
    degrees = {j: len((outgoing[j] | incoming[j]) - {j}) for j in ids}
    degree_counts = Counter(degrees.values())
    place_ids = {p['placeId'] for p in data['places']}
    connected = set()
    access_counts = Counter()
    distances = []
    for access in data['access']:
        if access['placeId'] in place_ids and str(access['junctionId']) in ids:
            connected.add(access['placeId'])
            access_counts[access['placeId']] += 1
        distance = access['properties'].get('accessDistanceM')
        if valid_distance(distance):
            distances.append(distance)
    n = len(ids)
    return dict(
        length=dict(totalKm=sum(lengths)/1000, averageM=sum(lengths)/len(lengths) if lengths else 0,
                    maximumM=max(lengths, default=0), invalidCount=len(data['roads'])-len(lengths),
                    histogram=distribution(lengths, [50, 100, 250, 500, 1000]),
                    byType=[dict(type=k, count=v) for k, v in sorted(by_type.items(), key=lambda item: -item[1])]),
        degree=dict(average=sum(degrees.values())/n if n else 0, isolated=sum(d == 0 for d in degrees.values()),
                    noIncoming=sum(not incoming[j] for j in ids), noOutgoing=sum(not outgoing[j] for j in ids),
                    histogram=[dict(type=f'{d} láng giềng', count=c) for d, c in sorted(degree_counts.items())],
                    top=[dict(type=j, count=d) for j, d in sorted(degrees.items(), key=lambda item: (-item[1], item[0]))[:10]]),
        connectivity=dict(weakCount=len(weak), strongCount=len(strong), largestWeak=max(weak, default=0),
                          largestStrong=max(strong, default=0), largestStrongPercent=100*max(strong, default=0)/n if n else 0,
                          weakSizes=[dict(type=f'Nhóm {i+1}', count=size) for i, size in enumerate(weak[:10])],
                          strongSizes=[dict(type=f'Nhóm {i+1}', count=size) for i, size in enumerate(strong[:10])]),
        access=dict(connected=len(connected), disconnected=len(place_ids-connected),
                    coveragePercent=100*len(connected)/len(place_ids) if place_ids else 0,
                    multiple=sum(count > 1 for count in access_counts.values()),
                    invalidCount=len(data['access'])-len(distances), histogram=distribution(distances, [50, 100, 250, 500, 1000])))


def analyze_network(data, warning_distance=500):
    # Use Neo4j element IDs for topology even when business IDs are duplicated.
    graph = dict(data)
    graph['junctions'] = [dict(j, junctionId=j.get('_key', j.get('junctionId'))) for j in data['junctions']]
    graph['places'] = [dict(p, placeId=p.get('_key', p.get('placeId'))) for p in data['places']]
    graph['roads'] = [dict(r, source=r.get('sourceKey', r['source']), target=r.get('targetKey', r['target'])) for r in data['roads']]
    graph['access'] = [dict(a, placeId=a.get('placeKey', a['placeId']), junctionId=a.get('junctionKey', a['junctionId'])) for a in data['access']]
    result = _base_analysis(graph)
    ins, outs, highway, flags = Counter(), Counter(), Counter(), Counter()
    longest, self_loops, unnamed = [], 0, 0
    for road, edge in zip(data['roads'], graph['roads']):
        ins[str(edge['target'])] += 1
        outs[str(edge['source'])] += 1
        self_loops += str(edge['source']) == str(edge['target'])
        props = road['properties']
        unnamed += not str(props.get('name') or '').strip()
        highway[str(props.get('highway') or 'Chưa phân loại')] += 1
        value = props.get('oneway')
        flag = 'true' if value in (True, 1, 'True', 'true', 'yes', '1') else 'false' if value in (False, 0, 'False', 'false', 'no', '0') else 'Thiếu / không hợp lệ'
        flags[flag] += 1
        if valid_distance(props.get('lengthM')):
            longest.append(dict(roadId=props.get('roadId'), name=props.get('name'), source=road['source'], target=road['target'], lengthM=props['lengthM']))
    junctions = {str(j.get('_key', j.get('junctionId'))): j for j in data['junctions']}
    degrees = {key: ins[key]+outs[key] for key in junctions}
    n = len(junctions)
    result['degree'].update(average=sum(degrees.values())/n if n else 0, maximum=max(degrees.values(), default=0),
                            averageIn=sum(ins.values())/n if n else 0, averageOut=sum(outs.values())/n if n else 0,
                            histogram=[dict(type=str(d), count=c) for d, c in sorted(Counter(degrees.values()).items())],
                            top=[dict(junctionId=junctions[key].get('junctionId'), degree=d, inDegree=ins[key], outDegree=outs[key]) for key, d in sorted(degrees.items(), key=lambda item: (-item[1], item[0]))[:10]])
    result['length'].update(minimumM=min((r['lengthM'] for r in longest), default=0), top=sorted(longest, key=lambda r: -r['lengthM'])[:10])
    c = result['connectivity']
    c.update(count=c['weakCount'], largest=c['largestWeak'], mainPercent=100*c['largestWeak']/n if n else 0, outside=n-c['largestWeak'])
    places = {str(p.get('_key', p.get('placeId'))): p for p in data['places']}
    farthest = {}
    distances = []
    for access, edge in zip(data['access'], graph['access']):
        distance = access['properties'].get('accessDistanceM')
        if not valid_distance(distance):
            continue
        distances.append(distance)
        key = str(edge['placeId'])
        row = dict(placeId=access['placeId'], name=places.get(key, {}).get('name'), junctionId=access['junctionId'], distanceM=distance)
        if key not in farthest or distance > farthest[key]['distanceM']:
            farthest[key] = row
    top = sorted(farthest.values(), key=lambda r: -r['distanceM'])
    warnings = [r for r in top if r['distanceM'] > warning_distance]
    result['access'].update(averageM=sum(distances)/len(distances) if distances else 0, minimumM=min(distances, default=0), maximumM=max(distances, default=0),
                            top=top[:10], warningDistanceM=warning_distance, warningCount=len(warnings), warnings=warnings[:50])
    duplicates = []
    for kind, rows, field in [('Junction', data['junctions'], 'junctionId'), ('Place', data['places'], 'placeId'), ('ROAD', [r['properties'] for r in data['roads']], 'roadId')]:
        counts = Counter(str(row[field]) for row in rows if row.get(field) is not None)
        duplicates.extend(dict(kind=kind, id=key, count=count) for key, count in sorted(counts.items()) if count > 1)
    def valid_coordinate(value, limit):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and -limit <= value <= limit
    result['quality'] = dict(missingCoordinates=sum(not valid_coordinate(j.get('latitude'), 90) or not valid_coordinate(j.get('longitude'), 180) for j in data['junctions']),
                             invalidLength=result['length']['invalidCount'], selfLoops=self_loops, unnamedRoads=unnamed,
                             missingAccess=result['access']['disconnected'], duplicateGroups=len(duplicates), duplicates=duplicates)
    result['overview'] = dict(junctions=len(data['junctions']), roads=len(data['roads']), places=len(data['places']), access=len(data['access']))
    result['highway'] = [dict(type=k, count=v) for k, v in sorted(highway.items(), key=lambda item: -item[1])]
    result['oneway'] = [dict(type=k, count=flags[k]) for k in ('true', 'false', 'Thiếu / không hợp lệ')]
    linked_places = {str(a['placeId']) for a in graph['access']}
    def road_row(road):
        return dict(roadId=road['properties'].get('roadId'), name=road['properties'].get('name'),
                    source=road['source'], target=road['target'])
    result['issues'] = dict(
        unlinkedPlaces=[dict(placeId=p.get('placeId'), name=p.get('name'), category=p.get('category'))
                        for p in data['places'] if str(p.get('_key', p.get('placeId'))) not in linked_places],
        missingLengthRoads=[road_row(r) for r in data['roads'] if r['properties'].get('lengthM') is None],
        selfLoopRoads=[road_row(r) for r, edge in zip(data['roads'], graph['roads']) if str(edge['source']) == str(edge['target'])],
        missingCoordinateJunctions=[dict(junctionId=j.get('junctionId'), latitude=j.get('latitude'), longitude=j.get('longitude'))
                                    for j in data['junctions'] if j.get('latitude') is None or j.get('longitude') is None])
    result['degree']['isolated'] = sum(degree == 0 for degree in degrees.values())
    return result
