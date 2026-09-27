import { fmtTime } from '../api/client'
import type { Notification } from '../types'

export default function NotificationsPanel({ items }: { items: Notification[] }) {
  if (items.length === 0) return <div className="px-3 py-4 text-center text-xs text-gray-400">No notifications</div>
  return (
    <div className="divide-y divide-gray-100">
      {items.map((n) => (
        <div key={n.id} className="px-2 py-1 text-[11px]">
          <div className="flex gap-1 text-gray-500"><span className="badge bg-gray-100 text-gray-600">{n.delivery_mode}</span><span>{n.recipient_type} {n.recipient_ref}</span><span className="ml-auto">{fmtTime(n.sim_time)}</span></div>
          <div className="text-gray-800">{n.message}</div>
        </div>
      ))}
    </div>
  )
}
