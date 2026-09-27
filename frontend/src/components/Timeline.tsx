import { minutesOfDay, fmtTime, techStatusColor } from '../api/client'
import type { Clock, Order, Technician } from '../types'

interface Props { techs: Technician[]; orders: Order[]; clock: Clock | null; selectedOrder: string | null; selectedTech: string | null; onSelectOrder: (id: string) => void; onSelectTech: (id: string) => void }

const START = 7 * 60, END = 19 * 60
const ROW = 34, LEFT = 130, W = 1100
const x = (m: number) => LEFT + ((m - START) / (END - START)) * (W - LEFT - 10)

export default function Timeline({ techs, orders, clock, selectedOrder, selectedTech, onSelectOrder, onSelectTech }: Props) {
  const orderById = new Map(orders.map((o) => [o.id, o]))
  const nowM = clock ? minutesOfDay(clock.now) : null
  const height = 24 + techs.length * ROW
  return (
    <div className="overflow-x-auto">
      <svg width={W} height={height} className="block select-none">
        {Array.from({ length: (END - START) / 60 + 1 }, (_, i) => START + i * 60).map((m) => (
          <g key={m}>
            <line x1={x(m)} x2={x(m)} y1={18} y2={height} stroke="#e5e7eb" />
            <text x={x(m)} y={12} fontSize={10} textAnchor="middle" fill="#6b7280">{String(m / 60).padStart(2, '0')}:00</text>
          </g>
        ))}
        {techs.map((t, i) => {
          const y = 24 + i * ROW
          const ss = minutesOfDay(t.shift_start), se = minutesOfDay(t.shift_end)
          const sel = selectedTech === t.id
          return (
            <g key={t.id}>
              <rect x={0} y={y} width={W} height={ROW} fill={sel ? '#eff6ff' : i % 2 ? '#fafafa' : '#fff'} />
              <g className="cursor-pointer" onClick={() => onSelectTech(t.id)}>
                <rect x={0} y={y} width={LEFT - 4} height={ROW} fill="transparent" />
                <text x={6} y={y + ROW / 2 + 4} fontSize={12} fontWeight={600} fill={sel ? '#1d4ed8' : techStatusColor[t.status] ?? '#111827'}>{t.name}</text>
                <title>{`${t.name} (${t.id}) · ${t.status}\nskills: ${Object.entries(t.skills).map(([k, v]) => `${k} L${v}`).join(', ')}\nclick for details`}</title>
              </g>
              <rect x={x(ss)} y={y + 6} width={x(se) - x(ss)} height={ROW - 12} fill="#f3f4f6" rx={3} />
              {t.breaks.map((b, j) => (
                <g key={j}>
                  <rect x={x(minutesOfDay(b.start))} y={y + 6} width={x(minutesOfDay(b.end)) - x(minutesOfDay(b.start))} height={ROW - 12} fill="url(#hatch)" opacity={b.status === 'done' ? 0.5 : 1} />
                  <title>{`rest ${fmtTime(b.start)}–${fmtTime(b.end)} · ${b.status ?? ''} · ${b.created_by ?? ''} · ${b.reason ?? ''}`}</title>
                </g>
              ))}
              {t.unavailable_intervals.map((u, j) => (
                <rect key={`u${j}`} x={x(Math.max(START, minutesOfDay(u.start)))} y={y + 6} width={Math.max(0, x(Math.min(END, minutesOfDay(u.end))) - x(Math.max(START, minutesOfDay(u.start))))} height={ROW - 12} fill="#fecaca" opacity={0.7} />
              ))}
              {t.route.map((r) => {
                const dep = minutesOfDay(r.departure), arr = minutesOfDay(r.arrival), st = minutesOfDay(r.service_start), en = minutesOfDay(r.service_end)
                const o = orderById.get(r.order_id)
                const isSel = selectedOrder === r.order_id
                const pri = o?.effective_priority ?? r.priority
                const fill = pri === 'P0' ? '#dc2626' : pri === 'P1' ? '#f97316' : pri === 'P2' ? '#fbbf24' : '#60a5fa'
                return (
                  <g key={r.order_id} className="cursor-pointer" onClick={() => onSelectOrder(r.order_id)}>
                    {arr > dep && <rect x={x(dep)} y={y + 11} width={x(arr) - x(dep)} height={ROW - 22} fill="#9ca3af" opacity={0.6} />}
                    {st > arr && <rect x={x(arr)} y={y + 11} width={x(st) - x(arr)} height={ROW - 22} fill="#d1d5db" opacity={0.8} />}
                    <rect x={x(st)} y={y + 7} width={Math.max(2, x(en) - x(st))} height={ROW - 14} fill={fill} rx={2} stroke={isSel ? '#111827' : 'none'} strokeWidth={2} />
                    {r.locked && <text x={x(st) + 3} y={y + 18} fontSize={9} fill="#fff">🔒</text>}
                    <text x={x(st) + (r.locked ? 16 : 3)} y={y + 19} fontSize={9} fill="#fff" fontWeight={600}>{r.order_id.replace('wo_', '')}</text>
                    <title>{`${r.order_id} · ${o?.catalog_snapshot.problem_name ?? ''}\ntravel ${fmtTime(r.departure)}→${fmtTime(r.arrival)} (${r.travel_minutes} min) · wait ${r.waiting_minutes} min\nservice ${fmtTime(r.service_start)}→${fmtTime(r.service_end)}${r.locked ? '\nLOCKED (departed)' : ''}${r.travel_minutes === 0 ? '\nsame location — no travel block' : ''}`}</title>
                  </g>
                )
              })}
            </g>
          )
        })}
        {nowM !== null && <line x1={x(nowM)} x2={x(nowM)} y1={14} y2={height} stroke="#ef4444" strokeWidth={1.5} strokeDasharray="4 2" />}
        <defs>
          <pattern id="hatch" width={6} height={6} patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1={0} y1={0} x2={0} y2={6} stroke="#9ca3af" strokeWidth={2} /></pattern>
        </defs>
      </svg>
      {/* Two groups, because they answer different questions: what a bar on the row means, and what the colour of a
          technician's name means. Mixing them in one wrapping line is what made this unreadable. */}
      <div className="space-y-0.5 border-t border-gray-100 px-2 pb-1 pt-1 text-[10px] leading-tight text-gray-500">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
          <span className="w-[86px] shrink-0 text-gray-400">bars on a row</span>
          <Swatch cls="bg-gray-400" label="travel" title="no grey block = 0 min travel (next job at the same location)" />
          <Swatch cls="bg-gray-300" label="wait" />
          <Swatch cls="bg-blue-400" label="service" note="colour = priority" />
          <Swatch style={{ background: 'repeating-linear-gradient(45deg,#9ca3af 0 2px,#fff 2px 4px)' }}
            label="rest" note={techs.every((t) => t.breaks.length === 0) ? 'dynamic · none planned yet' : 'dynamic'}
            title="休息是动态的：工作满 180 分钟后系统规划，或技师自行声明" />
          <Swatch cls="bg-red-200" label="unavailable" />
          <span className="whitespace-nowrap">🔒 <span className="text-gray-600">departed</span> <span className="text-gray-400">(locked)</span></span>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
          <span className="w-[86px] shrink-0 text-gray-400">name colour</span>
          {(['AVAILABLE', 'EN_ROUTE', 'ARRIVED', 'BUSY', 'BREAK', 'UNAVAILABLE'] as const).map((st) => (
            <span key={st} className="whitespace-nowrap font-semibold" style={{ color: techStatusColor[st] }}>{st.toLowerCase().replace('_', ' ')}</span>
          ))}
        </div>
      </div>
    </div>
  )
}

function Swatch({ cls, style, label, note, title }: { cls?: string; style?: React.CSSProperties; label: string; note?: string; title?: string }) {
  return (
    <span className="flex items-center gap-1 whitespace-nowrap" title={title}>
      <i className={`inline-block h-2.5 w-3.5 rounded-sm ${cls ?? ''}`} style={style} />
      <span className="text-gray-600">{label}</span>
      {note && <span className="text-gray-400">({note})</span>}
    </span>
  )
}
