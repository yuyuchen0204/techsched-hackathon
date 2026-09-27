import { fmtTime } from '../api/client'
import { SkillBar } from './TechnicianDetail'
import type { Technician } from '../types'

const techStatusCls: Record<string, string> = { AVAILABLE: 'bg-green-100 text-green-800', EN_ROUTE: 'bg-indigo-100 text-indigo-800', ARRIVED: 'bg-violet-100 text-violet-800', BUSY: 'bg-purple-100 text-purple-800', UNAVAILABLE: 'bg-red-100 text-red-800', OFF_SHIFT: 'bg-gray-200 text-gray-600' }

export default function TechniciansPanel({ techs, onSelect }: { techs: Technician[]; onSelect: (id: string) => void }) {
  return (
    <div className="divide-y divide-gray-100">
      {techs.map((t) => (
        <button key={t.id} className="block w-full px-3 py-2 text-left text-xs hover:bg-blue-50" onClick={() => onSelect(t.id)}>
          <div className="flex items-center gap-2"><span className="font-semibold">{t.name}</span><span className="font-mono text-gray-400">{t.id}</span><span className={`badge ${techStatusCls[t.status] ?? 'bg-gray-100'}`}>{t.status}</span>
            <span className="ml-auto text-gray-500">{fmtTime(t.shift_start)}–{fmtTime(t.shift_end)} · {t.route.length} task(s)</span></div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
            {Object.entries(t.skills).sort((a, b) => b[1] - a[1]).map(([trade, level]) => <span key={trade} className="inline-flex items-center gap-1 text-gray-700"><SkillBar level={level} /><span>{trade}</span></span>)}
          </div>
          <div className="mt-0.5 text-[10px] text-gray-400">next free: {t.anchor.location_name} @ {fmtTime(t.anchor.time)}{t.unavailable_intervals.length ? ` · unavailable ${t.unavailable_intervals.map((u) => `${fmtTime(u.start)}–${fmtTime(u.end)}`).join(', ')}` : ''}</div>
        </button>
      ))}
    </div>
  )
}
