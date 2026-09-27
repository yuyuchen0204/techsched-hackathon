import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import { api } from '../api/client'
import { dropPin } from './mapPin'
import type { AddressResult, SavedAddress } from '../types'

export interface AddressPayload {
  latitude: number; longitude: number; formatted_address: string; postal_code?: string | null; building_name?: string | null; street_address?: string | null
  unit_number?: string | null; unit_not_applicable: boolean; source: string; save: boolean
}
interface Props {
  prefill?: Partial<AddressPayload> | Record<string, unknown> | null
  saved?: SavedAddress[]
  busy: boolean
  onConfirm: (p: AddressPayload) => void
  onUseSaved?: (id: string) => void
  onCancel: () => void
}

/** Address entry (§7.1): search (OneMap / Nominatim via the backend), postal code, map pin — plus a mandatory unit/floor
 *  or an explicit "no unit" mark. The coordinates always come from a search result or the pin, never from free text. */
export default function AddressModal({ prefill, saved = [], busy, onConfirm, onUseSaved, onCancel }: Props) {
  const p = (prefill ?? {}) as Record<string, unknown>
  const [mode, setMode] = useState<'search' | 'map'>('search')
  const [q, setQ] = useState('')
  const [results, setResults] = useState<AddressResult[]>([])
  const [provider, setProvider] = useState<string | null>(null)
  const [searching, setSearching] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [picked, setPicked] = useState<{ lat: number; lon: number; label: string; postal?: string | null; source: string } | null>(
    typeof p.latitude === 'number' && typeof p.longitude === 'number'
      ? { lat: p.latitude as number, lon: p.longitude as number, label: String(p.formatted_address ?? ''), postal: (p.postal_code as string | null) ?? null, source: String(p.source ?? 'search') }
      : null)
  const [unit, setUnit] = useState(String(p.unit_number ?? ''))
  const [noUnit, setNoUnit] = useState(Boolean(p.unit_not_applicable))
  const [save, setSave] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (q.trim().length < 3) { setResults([]); return }
    const t = setTimeout(() => {
      setSearching(true)
      api.addressSearch(q.trim()).then((r) => { setResults(r.results); setProvider(r.provider); setNote(r.note ?? (r.degraded ? `lookup degraded: ${r.reason}` : null)) })
        .catch((e) => setNote((e as Error).message)).finally(() => setSearching(false))
    }, 350)
    return () => clearTimeout(t)
  }, [q])

  const confirm = () => {
    setErr(null)
    if (!picked) { setErr('Choose a search result or drop a pin on the map first.'); return }
    if (!unit.trim() && !noUnit) { setErr('Unit / floor is required — or tick "no unit (landed / shop)".'); return }
    onConfirm({ latitude: picked.lat, longitude: picked.lon, formatted_address: picked.label, postal_code: picked.postal ?? null, street_address: picked.label,
      unit_number: unit.trim() || null, unit_not_applicable: noUnit && !unit.trim(), source: picked.source, save })
  }
  return (
    <div className="fixed inset-0 z-[700] flex items-end justify-center bg-black/40 sm:items-center" onClick={onCancel}>
      <div className="flex max-h-[92vh] w-full max-w-md flex-col rounded-t-2xl bg-white shadow-xl sm:rounded-2xl" onClick={(e) => e.stopPropagation()} data-testid="address-modal">
        <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
          <div className="text-sm font-semibold">Service address</div>
          <button className="text-xs text-gray-500" onClick={onCancel}>close</button>
        </div>
        <div className="min-h-0 flex-1 space-y-3 overflow-auto px-4 py-3 text-sm">
          {saved.length > 0 && onUseSaved && (
            <div>
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-500">Saved addresses</div>
              <div className="space-y-1">
                {saved.map((a) => (
                  <button key={a.id} className="flex w-full items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-left hover:bg-blue-50" disabled={busy} onClick={() => onUseSaved(a.id)}>
                    <span className="text-lg">🏠</span>
                    <span className="min-w-0 flex-1"><span className="block truncate">{a.formatted_address}</span><span className="text-xs text-gray-500">{a.unit_number ? `#${a.unit_number}` : a.unit_not_applicable ? 'no unit' : 'unit missing'}{a.is_default ? ' · default' : ''}</span></span>
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="flex gap-1 rounded-lg bg-gray-100 p-0.5 text-xs">
            <button className={`flex-1 rounded-md py-1 ${mode === 'search' ? 'bg-white shadow' : 'text-gray-500'}`} onClick={() => setMode('search')}>Search address / postal code</button>
            <button className={`flex-1 rounded-md py-1 ${mode === 'map' ? 'bg-white shadow' : 'text-gray-500'}`} onClick={() => setMode('map')}>Drop a pin</button>
          </div>
          {mode === 'search' && (
            <div>
              <input data-testid="address-search" className="w-full rounded-lg border px-3 py-2" placeholder="e.g. 123 Tampines St 11 or 521123" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
              <div className="mt-1 text-[11px] text-gray-500">{searching ? 'searching…' : provider ? `results from ${provider}` : 'type at least 3 characters'}{note ? ` · ${note}` : ''}</div>
              <div className="mt-1 max-h-48 space-y-1 overflow-auto">
                {results.map((r, i) => (
                  <button key={i} data-testid="address-result" className={`block w-full rounded-lg border px-3 py-1.5 text-left text-xs hover:bg-blue-50 ${picked?.label === r.address ? 'border-blue-500 bg-blue-50' : 'border-gray-200'}`}
                    onClick={() => setPicked({ lat: r.lat, lon: r.lon, label: r.address, postal: r.postal, source: r.source })}>
                    <div className="font-medium">{r.name && r.name !== r.address ? r.name : r.address}</div>
                    {r.name && r.name !== r.address && <div className="text-gray-500">{r.address}</div>}
                    <div className="text-[10px] text-gray-400">{r.postal ? `S${r.postal} · ` : ''}{r.source}</div>
                  </button>
                ))}
              </div>
            </div>
          )}
          {mode === 'map' && <PinMap initial={picked ? [picked.lat, picked.lon] : null} onPin={(lat, lon) => {
            // never label a pin with its own coordinates: ask the backend to reverse-geocode it to a real address
            setPicked({ lat, lon, label: 'looking up the address…', source: 'map_pin' })
            api.resolvePoint(lat, lon).then((r) => setPicked({ lat, lon, label: r.location.name, postal: r.postal_code ?? null, source: 'map_pin' }))
              .catch(() => setPicked({ lat, lon, label: `Pinned point (${lat.toFixed(4)}, ${lon.toFixed(4)})`, source: 'map_pin' }))
          }} />}
          {picked && <div className="rounded-lg bg-blue-50 px-3 py-2 text-xs"><span className="font-semibold">Selected:</span> {picked.label}{picked.postal ? ` · S${picked.postal}` : ''} <span className="text-gray-500">({picked.source})</span></div>}
          <div>
            <label className="block text-xs font-medium text-gray-700">Unit / floor number <span className="text-red-600">*</span></label>
            <input data-testid="address-unit" className="mt-1 w-full rounded-lg border px-3 py-2" placeholder="#05-123" value={unit} onChange={(e) => setUnit(e.target.value)} disabled={noUnit} />
            <label className="mt-1 flex items-center gap-2 text-xs text-gray-600"><input type="checkbox" checked={noUnit} onChange={(e) => setNoUnit(e.target.checked)} /> no unit (landed house / shop front)</label>
          </div>
          <label className="flex items-center gap-2 text-xs text-gray-600"><input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} /> save as my default address (untick = this time only)</label>
          {err && <div className="rounded bg-red-50 px-3 py-2 text-xs text-red-700">{err}</div>}
        </div>
        <div className="flex gap-2 border-t border-gray-100 px-4 py-3">
          <button className="btn" onClick={onCancel}>Cancel</button>
          <button data-testid="address-confirm" className="btn btn-primary flex-1 justify-center" disabled={busy} onClick={confirm}>Confirm address</button>
        </div>
      </div>
    </div>
  )
}

function PinMap({ initial, onPin }: { initial: [number, number] | null; onPin: (lat: number, lon: number) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map | null>(null)
  const markerRef = useRef<L.Marker | null>(null)
  useEffect(() => {
    if (!ref.current || mapRef.current) return
    const map = L.map(ref.current, { center: initial ?? [1.35, 103.85], zoom: initial ? 15 : 11 })
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap' }).addTo(map)
    if (initial) markerRef.current = L.marker(initial, { icon: dropPin() }).addTo(map)
    map.on('click', (e: L.LeafletMouseEvent) => {
      if (markerRef.current) markerRef.current.setLatLng(e.latlng)
      else markerRef.current = L.marker(e.latlng, { icon: dropPin() }).addTo(map)
      onPin(e.latlng.lat, e.latlng.lng)
    })
    mapRef.current = map
    const t = setTimeout(() => map.invalidateSize(), 50)
    return () => { clearTimeout(t); map.stop(); map.remove(); mapRef.current = null; markerRef.current = null }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return <div><div ref={ref} className="h-56 w-full rounded-lg" /><div className="mt-1 text-[11px] text-gray-500">Tap the map to place the pin at your door.</div></div>
}
