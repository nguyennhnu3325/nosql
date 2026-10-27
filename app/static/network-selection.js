/* Shared selection for search results and Leaflet map context. */
(function (root) {
  function selectNetwork(data, rows, filtered) {
    if (!filtered) return data;
    const junctionIds = new Set(), placeIds = new Set();
    const roadIds = new Set(), accessIds = new Set();
    const roadKey = r => r.elementId ?? JSON.stringify([String(r.source), String(r.target), (r.properties ?? r).roadId]);
    const accessKey = r => r.elementId ?? JSON.stringify([String(r.placeId), String(r.junctionId)]);
    for (const row of rows) {
      if (row.kind === 'junctions') junctionIds.add(String(row.junctionId));
      if (row.kind === 'places') placeIds.add(String(row.placeId));
      if (row.kind === 'roads') roadIds.add(roadKey(row));
      if (row.kind === 'access') accessIds.add(accessKey(row));
    }
    const roads = data.roads.filter(r => roadIds.has(roadKey(r)));
    const access = data.access.filter(r => accessIds.has(accessKey(r)) || placeIds.has(String(r.placeId)));
    for (const r of roads) {
      junctionIds.add(String(r.source)); junctionIds.add(String(r.target));
    }
    for (const r of access) {
      placeIds.add(String(r.placeId)); junctionIds.add(String(r.junctionId));
    }
    return {
      junctions: data.junctions.filter(j => junctionIds.has(String(j.junctionId))),
      places: data.places.filter(p => placeIds.has(String(p.placeId))),
      roads, access
    };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = selectNetwork;
  else root.selectNetwork = selectNetwork;
})(typeof window !== 'undefined' ? window : globalThis);
