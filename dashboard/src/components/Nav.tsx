import Link from "next/link";
import { signOutAction } from "@/app/login/actions";
import { RealtimeIndicator } from "./RealtimeIndicator";

const links = [
  { href: "/", label: "Overview" },
  { href: "/positions", label: "Positions" },
  { href: "/trades", label: "Trades" },
  { href: "/signals", label: "Signals" },
  { href: "/system", label: "System" },
] as const;

export function Nav() {
  return (
    <nav className="border-b border-neutral-800 bg-neutral-950">
      <div className="mx-auto flex max-w-6xl items-center gap-1 px-6 py-3">
        <span className="mr-4 text-sm font-semibold tracking-tight">Trade AI</span>
        {links.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className="rounded px-3 py-1.5 text-sm text-neutral-300 hover:bg-neutral-800 hover:text-neutral-50"
          >
            {l.label}
          </Link>
        ))}
        <div className="ml-auto">
          <RealtimeIndicator />
        </div>
        <form action={signOutAction}>
          <button
            className="rounded px-3 py-1.5 text-sm text-neutral-400 hover:bg-neutral-800 hover:text-neutral-50"
            type="submit"
          >
            Sign out
          </button>
        </form>
      </div>
    </nav>
  );
}
