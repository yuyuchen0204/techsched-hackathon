import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { AgentSkill } from '../types'

/** What each agent was actually told, read straight from config/agent_skills/*.md.
 *  The point of showing it is the second column: tools the role gate permits but the playbook withholds — that is
 *  where a hand-off to another agent becomes mandatory rather than optional. */
export default function AgentSkills({ notice }: { notice: (m: string, k?: 'ok' | 'err') => void }) {
  const [skills, setSkills] = useState<AgentSkill[]>([])
  const [dir, setDir] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const load = () => api.agentSkills().then((r) => { setSkills(r.skills); setDir(r.directory) }).catch(() => setSkills([]))
  useEffect(() => { load() }, [])
  return (
    <div className="text-[11px]">
      <div className="flex items-center gap-2 border-b border-gray-100 px-2 py-1 text-[10px] text-gray-500">
        <span>{skills.length} playbooks in <span className="font-mono text-gray-600">{dir}</span></span>
        <button className="ml-auto text-blue-700 hover:underline"
          onClick={() => api.reloadSkills().then((r) => { load(); notice(`Reloaded ${r.reloaded.length} skills`) }).catch((e) => notice((e as Error).message, 'err'))}>
          reload from disk
        </button>
      </div>
      <div className="divide-y divide-gray-100">
        {skills.map((s) => (
          <div key={s.name} className="px-2 py-1.5">
            <div role="button" tabIndex={0} className="cursor-pointer" data-testid="skill-toggle"
              onClick={() => setOpen(open === s.name ? null : s.name)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen(open === s.name ? null : s.name) } }}>
              <div className="flex items-center gap-1">
                <span className="badge bg-gray-900 text-white">{s.name}</span>
                <span className="font-semibold text-gray-800">{s.title}</span>
                <span className="ml-auto text-gray-400">
                  {s.tools.length} tools · {s.max_tool_calls ?? '—'} calls · {s.max_searches ?? '—'} searches
                </span>
              </div>
              <div className="mt-0.5 line-clamp-2 text-gray-600">{s.description}</div>
            </div>
            {open === s.name && (
              <div className="mt-1 space-y-1.5">
                <div className="flex flex-wrap gap-1">
                  {s.tools.map((t) => <span key={t} className="rounded bg-green-50 px-1 font-mono text-[10px] text-green-800 ring-1 ring-green-200">{t}</span>)}
                </div>
                {s.withheld_by_skill.length > 0 && (
                  <div data-testid="skill-withheld">
                    <div className="text-[10px] font-semibold uppercase text-gray-400">withheld by this playbook</div>
                    <div className="flex flex-wrap gap-1">
                      {s.withheld_by_skill.map((t) => <span key={t} className="rounded bg-red-50 px-1 font-mono text-[10px] text-red-700 line-through ring-1 ring-red-200">{t}</span>)}
                    </div>
                    <div className="text-[10px] text-gray-400">the role is permitted these; the playbook is not, so this agent must delegate or escalate instead</div>
                  </div>
                )}
                {s.escalate_when.length > 0 && (
                  <div>
                    <div className="text-[10px] font-semibold uppercase text-gray-400">must escalate when</div>
                    <ul className="list-disc pl-4 text-gray-600">{s.escalate_when.map((x) => <li key={x}>{x}</li>)}</ul>
                  </div>
                )}
                <details className="rounded bg-gray-50 p-1.5">
                  <summary className="cursor-pointer text-[10px] font-semibold uppercase text-gray-500">full playbook ({s.source})</summary>
                  <pre className="mt-1 whitespace-pre-wrap font-mono text-[10px] leading-snug text-gray-700">{s.body}</pre>
                </details>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
