import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import { api, splitPolyline, techStatusColor } from '../api/client'
import type { LocationRef, Order, TechPosition, Technician, TechnicianRoute } from '../types'

interface Props { techs: Technician[]; orders: Order[]; locations: LocationRef[]; positions?: TechPosition[]; selectedOrder: string | null; selectedTech: string | null; affectedIds: string[]; onSelectOrder: (id: string) => void; onSelectTech?: (id: string) => void }

const priColor: Record<string, string> = { P0: '#dc2626', P1: '#f97316', P2: '#f59e0b', P3: '#3b82f6' }

export default function MapView(props: Props) {
  const [fallback, setFallback] = useState(false)
  const [route, setRoute] = useState<TechnicianRoute | null>(null)
  const { techs, selectedTech, selectedOrder } = props
  const routeTechId = selectedTech ?? (selectedOrder ? techs.find((t) => t.route.some((r) => r.order_id === selectedOrder))?.id ?? null : null)
  const routeKey = routeTechId ? techs.find((t) => t.id === routeTechId)?.route.map((r) => `${r.order_id}@${r.departure}`).join(',') : ''
  useEffect(() => {
    let alive = true
    if (!routeTechId) { setRoute(null); return }
    api.technicianRoute(routeTechId).then((r) => alive && setRoute(r)).catch(() => alive && setRoute(null))
    return () => { alive = false }
  }, [routeTechId, routeKey])
  const realRoads = route && route.legs.length > 0 && route.legs.every((l) => !l.schematic)
  return (
    <div className="relative h-full min-h-[320px]">
      {fallback ? <SchematicMap {...props} /> : <LeafletMap {...props} route={route} onTileError={() => setFallback(true)} />}
      <div className="pointer-events-none absolute bottom-1 left-1 z-[500] flex flex-col gap-0.5 rounded bg-white/85 px-2 py-1 text-[10px] text-gray-600">
        <div className="flex items-center gap-2">
          <span><i className="mr-0.5 inline-block h-2.5 w-2.5 rounded-full bg-red-600 align-middle" />P0</span>
          <span><i className="mr-0.5 inline-block h-2.5 w-2.5 rounded-full bg-orange-500 align-middle" />P1</span>
          <span><i className="mr-0.5 inline-block h-2.5 w-2.5 rounded-full bg-amber-400 align-middle" />P2</span>
          <span><i className="mr-0.5 inline-block h-2.5 w-2.5 rounded-full bg-blue-500 align-middle" />P3 order</span>
          <span><i className="mr-0.5 inline-block h-0.5 w-4 bg-blue-600 align-middle" />route (solid = road geometry)</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-gray-500">technician:</span>
          {(['AVAILABLE', 'EN_ROUTE', 'ARRIVED', 'BUSY', 'BREAK', 'UNAVAILABLE'] as const).map((s) => <span key={s}><i className="mr-0.5 inline-block h-2.5 w-2.5 rounded-full align-middle" style={{ background: techStatusColor[s] }} />{s.toLowerCase().replace('_', ' ')}</span>)}
          <span className="text-gray-400">· positions simulated from route geometry + sim clock</span>
        </div>
        <div>{fallback ? 'schematic map (tiles unavailable)' : 'OpenStreetMap tiles'} · routes: {route ? (realRoads ? `${route.route_provider} road geometry${route.degraded ? ' (DEGRADED)' : ''}` : 'straight-line schematic') : 'select a technician or order'} · synthetic demand</div>
      </div>
      {!fallback && <button className="absolute right-1 top-1 z-[500] rounded bg-white/90 px-1.5 py-0.5 text-[10px] text-gray-600 shadow" onClick={() => setFallback(true)}>schematic view</button>}
      {fallback && <button className="absolute right-1 top-1 z-[500] rounded bg-white/90 px-1.5 py-0.5 text-[10px] text-gray-600 shadow" onClick={() => setFallback(false)}>tile map</button>}
    </div>
  )
}

function LeafletMap({ techs, orders, locations, positions, selectedOrder, selectedTech, affectedIds, onSelectOrder, onSelectTech, onTileError, route }: Props & { onTileError: () => void; route: TechnicianRoute | null }) {
  const ref = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map | null>(null)
  const layerRef = useRef<L.LayerGroup | null>(null)
  const techLayerRef = useRef<L.LayerGroup | null>(null)
  // technician markers persist across polls: setLatLng + a CSS transform transition = continuous motion (no re-create → no jump)
  const techMarkers = useRef<Map<string, { m: L.Marker; iconKey: string }>>(new Map())
  useEffect(() => {
    if (!ref.current || mapRef.current) return
    const map = L.map(ref.current, { center: [1.345, 103.85], zoom: 12, zoomControl: true })
    const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap' })
    let errors = 0
    tiles.on('tileerror', () => { errors += 1; if (errors >= 3) onTileError() })
    tiles.addTo(map)
    layerRef.current = L.layerGroup().addTo(map)
    techLayerRef.current = L.layerGroup().addTo(map)
    // no glide while Leaflet itself re-positions markers (zoom / view reset), only between position polls
    const box = map.getContainer()
    map.on('zoomstart viewreset', () => box.classList.add('no-glide'))
    map.on('zoomend', () => setTimeout(() => box.classList.remove('no-glide'), 50))
    mapRef.current = map
    return () => { techMarkers.current.clear(); map.remove(); mapRef.current = null }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    const layer = layerRef.current
    if (!layer) return
    layer.clearLayers()
    const locById = new Map(locations.map((l) => [l.id, l]))
    for (const o of orders) {
      if (o.lifecycle_status === 'CANCELLED' || o.lifecycle_status === 'COMPLETED') continue
      const sel = o.id === selectedOrder
      const aff = affectedIds.includes(o.id)
      const m = L.circleMarker([o.location.lat, o.location.lon], { radius: sel ? 9 : 6, color: sel ? '#111827' : aff ? '#7c3aed' : priColor[o.effective_priority], weight: sel || aff ? 3 : 1.5, fillColor: priColor[o.effective_priority], fillOpacity: 0.85 })
      m.bindTooltip(`${o.id} · ${o.effective_priority} · ${o.catalog_snapshot.problem_name}<br/>${o.location.name}${o.location.id.startsWith('pt_') ? ' (map pin)' : ''} · ${o.lifecycle_status}`)
      m.on('click', () => onSelectOrder(o.id))
      m.addTo(layer)
    }
    const perSpot = new Map<string, number>()
    const posById = new Map((positions ?? []).map((p) => [p.technician_id, p]))
    const techLayer = techLayerRef.current
    const seen = new Set<string>()
    for (const t of techs) {
      const pos = posById.get(t.id)
      const loc = locById.get(t.anchor.location_id) ?? locById.get(t.current_location_id)
      const coords: [number, number] | null = pos ? pos.current_coords : loc ? [loc.lat, loc.lon] : null
      if (!coords) continue
      const spot = `${coords[0].toFixed(4)},${coords[1].toFixed(4)}`
      const n = pos?.moving ? 0 : perSpot.get(spot) ?? 0   // stacked offsets only for markers parked at the same place
      if (!pos?.moving) perSpot.set(spot, n + 1)
      const initials = t.name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()
      const status = pos?.status ?? t.status
      const bg = techStatusColor[status] ?? '#111827'
      const moving = pos?.moving ? 'outline:2px solid #4f46e5;outline-offset:2px;' : ''
      const iconKey = `${bg}|${selectedTech === t.id}|${moving}|${n}|${initials}`
      const where = pos?.moving ? `en route to ${pos.next_destination?.name ?? '?'} (${Math.round((pos.leg_progress ?? 0) * 100)}%, ETA ${pos.next_destination?.eta?.slice(11, 16) ?? '?'})` : pos?.current_service_location ? `at ${pos.current_service_location.name} (${pos.current_service_location.order_id})` : `next free: ${t.anchor.location_name} @ ${t.anchor.time.slice(11, 16)}`
      const tip = `<b>${t.name}</b> · ${status}<br/>${where}<br/>skills: ${Object.entries(t.skills).map(([k, v]) => `${k} L${v}`).join(', ')}`
      seen.add(t.id)
      if (techLayer) {
        const existing = techMarkers.current.get(t.id)
        if (existing) {
          if (existing.iconKey !== iconKey) {
            existing.m.setIcon(L.divIcon({ className: 'tech-marker', iconSize: [26, 26], iconAnchor: [13 - n * 14, 13], html: techIconHtml(bg, selectedTech === t.id, moving, initials) }))
            existing.iconKey = iconKey
          }
          existing.m.setLatLng(coords)
          existing.m.setTooltipContent(tip)
        } else {
          const m = L.marker(coords, { icon: L.divIcon({ className: 'tech-marker', iconSize: [26, 26], iconAnchor: [13 - n * 14, 13], html: techIconHtml(bg, selectedTech === t.id, moving, initials) }), zIndexOffset: 1000 })
          m.bindTooltip(tip)
          m.on('click', () => onSelectTech?.(t.id))
          m.addTo(techLayer)
          techMarkers.current.set(t.id, { m, iconKey })
        }
      }
      if (selectedTech === t.id || (selectedOrder && t.route.some((r) => r.order_id === selectedOrder))) {
        if (route && route.technician_id === t.id && route.legs.length > 0) {
          // real road geometry per leg (solid); schematic legs dashed
          for (const leg of route.legs) {
            const tip = `${leg.from_id} → ${leg.to_id} (${leg.order_id}): ${leg.minutes} min planned${leg.distance_km != null ? ` · ${leg.distance_km} km` : ''}${leg.provider_minutes != null ? ` · ${leg.provider_minutes} min free-flow` : ''}`
            const style = { color: leg.locked ? '#7c3aed' : '#2563eb', weight: 4, opacity: 0.85, dashArray: leg.schematic ? '6 4' : undefined }
            // the leg being driven right now shrinks as the technician advances; the part already covered stays as a faint trail.
            // Matched on current_leg (order + origin), never on next_destination: that one names the job queued AFTER this drive.
            const cur = pos?.current_leg
            const driving = Boolean(pos?.moving && cur && cur.progress != null
              && cur.order_id === leg.order_id && cur.from_location_id === leg.from_id)
            if (driving) {
              const from = locById.get(leg.from_id)
              // position_service measures the fraction from the technician's real origin, so match its point list exactly
              const pts: [number, number][] = from && leg.points.length > 0 && (leg.points[0][0] !== from.lat || leg.points[0][1] !== from.lon)
                ? [[from.lat, from.lon], ...leg.points] : leg.points
              const [done, left] = splitPolyline(pts, cur!.progress!)
              if (done.length > 1) L.polyline(done, { ...style, color: '#9ca3af', weight: 3, opacity: 0.45, dashArray: '4 5' }).addTo(layer)
              if (left.length > 1) L.polyline(left, style).bindTooltip(`${tip} · ${Math.round((cur!.progress ?? 0) * 100)}% driven`).addTo(layer)
            } else {
              L.polyline(leg.points, style).bindTooltip(tip).addTo(layer)
            }
          }
        } else {
          const pts: [number, number][] = []
          let prev = locById.get(t.route[0]?.origin_location_id ?? t.current_location_id)
          if (prev) pts.push([prev.lat, prev.lon])
          for (const r of t.route) {
            const l = locById.get(r.location_id)
            if (l) pts.push([l.lat, l.lon])
            prev = l
          }
          if (pts.length > 1) L.polyline(pts, { color: '#2563eb', weight: 3, dashArray: '6 4', opacity: 0.8 }).addTo(layer)
        }
      }
    }
    for (const [id, entry] of techMarkers.current) {
      if (!seen.has(id)) { entry.m.remove(); techMarkers.current.delete(id) }
    }
  }, [techs, orders, locations, positions, selectedOrder, selectedTech, affectedIds, onSelectOrder, onSelectTech, route])
  return <div ref={ref} className="h-full w-full" />
}

function techIconHtml(bg: string, selected: boolean, moving: string, initials: string): string {
  return `<div style="width:26px;height:26px;border-radius:50%;background:${bg};color:#fff;font:700 10px/26px system-ui;text-align:center;border:${selected ? '3px solid #111827' : '2px solid #fff'};box-shadow:0 1px 3px rgba(0,0,0,.4);cursor:pointer;${moving}">${initials}</div>`
}

function SchematicMap({ techs, orders, locations, selectedOrder, selectedTech, affectedIds, onSelectOrder, onSelectTech }: Props) {
  const lats = locations.map((l) => l.lat), lons = locations.map((l) => l.lon)
  const minLat = Math.min(...lats) - 0.01, maxLat = Math.max(...lats) + 0.01, minLon = Math.min(...lons) - 0.01, maxLon = Math.max(...lons) + 0.01
  const W = 600, H = 340
  const px = (lon: number) => ((lon - minLon) / (maxLon - minLon)) * (W - 40) + 20
  const py = (lat: number) => H - (((lat - minLat) / (maxLat - minLat)) * (H - 40) + 20)
  const locById = new Map(locations.map((l) => [l.id, l]))
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-full w-full bg-slate-50">
      <text x={8} y={14} fontSize={10} fill="#64748b">SCHEMATIC · not to scale</text>
      {locations.map((l) => <g key={l.id}><circle cx={px(l.lon)} cy={py(l.lat)} r={2} fill="#cbd5e1" /><text x={px(l.lon) + 4} y={py(l.lat) - 4} fontSize={8} fill="#94a3b8">{l.area ?? l.name}</text></g>)}
      {techs.map((t) => {
        if (!(selectedTech === t.id || (selectedOrder && t.route.some((r) => r.order_id === selectedOrder)))) return null
        const pts: [number, number][] = []
        const start = locById.get(t.route[0]?.origin_location_id ?? t.current_location_id)
        if (start) pts.push([px(start.lon), py(start.lat)])
        for (const r of t.route) { const l = locById.get(r.location_id); if (l) pts.push([px(l.lon), py(l.lat)]) }
        return <polyline key={t.id} points={pts.map((p) => p.join(',')).join(' ')} fill="none" stroke="#2563eb" strokeWidth={2} strokeDasharray="5 3" />
      })}
      {orders.filter((o) => !['CANCELLED', 'COMPLETED'].includes(o.lifecycle_status)).map((o) => (
        <g key={o.id} className="cursor-pointer" onClick={() => onSelectOrder(o.id)}>
          <circle cx={px(o.location.lon)} cy={py(o.location.lat)} r={o.id === selectedOrder ? 8 : 5} fill={priColor[o.effective_priority]} stroke={o.id === selectedOrder ? '#111827' : affectedIds.includes(o.id) ? '#7c3aed' : '#fff'} strokeWidth={2} />
          <title>{`${o.id} · ${o.effective_priority} · ${o.catalog_snapshot.problem_name} · ${o.location.name}`}</title>
        </g>
      ))}
      {techs.map((t, i) => { const l = locById.get(t.anchor.location_id); if (!l) return null; const ini = t.name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase(); return <g key={t.id} className="cursor-pointer" onClick={() => onSelectTech?.(t.id)}><circle cx={px(l.lon) + (i % 3) * 6} cy={py(l.lat) - 10} r={8} fill={t.status === 'UNAVAILABLE' ? '#dc2626' : '#111827'} stroke="#fff" /><text x={px(l.lon) + (i % 3) * 6} y={py(l.lat) - 7} fontSize={7} fill="#fff" fontWeight={700} textAnchor="middle">{ini}</text><title>{t.name} · {t.status}</title></g> })}
    </svg>
  )
}
