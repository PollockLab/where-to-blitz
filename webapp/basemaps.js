/* Browser raster sources. Keep each provider's credit attached to its own source. */
const BasemapCatalog=(()=>{
  const OSM='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
  const CARTO=OSM+' · &copy; <a href="https://carto.com/attribution/">CARTO</a>';
  const TERRAIN=OSM+', SRTM · &copy; <a href="https://opentopomap.org/">OpenTopoMap</a> (<a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA</a>)';
  const IMAGERY='Source: <a href="https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9">Esri</a>, Vantor, Earthstar Geographics, and the GIS User Community';
  const osm=()=>({tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],maxzoom:19,attribution:OSM});
  function create(apiKey){
    const key=typeof apiKey==='string'?apiKey.trim():'';
    const carto=style=>({
      tiles:['https://basemaps.cartocdn.com/'+style+'/{z}/{x}/{y}.png?key='+encodeURIComponent(key)],
      maxzoom:20,attribution:CARTO
    });
    // Unkeyed CARTO responds with a successful HTTP status and an error image.
    // Ordinary browser OSM requests retain the provider's cache and Referer headers.
    return {
      Standard:key?carto('rastertiles/voyager'):osm(),
      Satellite:{tiles:['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],maxzoom:19,attribution:IMAGERY},
      Terrain:{tiles:['https://a.tile.opentopomap.org/{z}/{x}/{y}.png','https://b.tile.opentopomap.org/{z}/{x}/{y}.png','https://c.tile.opentopomap.org/{z}/{x}/{y}.png'],maxzoom:17,attribution:TERRAIN},
      Light:key?carto('light_all'):osm()
    };
  }
  return {create};
})();
