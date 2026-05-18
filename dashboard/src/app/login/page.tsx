import { headers } from "next/headers";
import { signInAction } from "./actions";

export const dynamic = "force-dynamic";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; next?: string }>;
}) {
  const [{ error, next }, headerStore] = await Promise.all([searchParams, headers()]);
  const host = headerStore.get("x-forwarded-host") ?? headerStore.get("host") ?? "";
  const protocol = headerStore.get("x-forwarded-proto") ?? "http";
  const origin = host ? `${protocol}://${host}` : "";

  return (
    <main className="mx-auto flex min-h-[70vh] max-w-md flex-col justify-center px-6 py-10">
      <h1 className="text-xl font-semibold tracking-tight">Trade AI Dashboard</h1>
      <p className="mt-2 text-sm text-neutral-400">運用ダッシュボードにサインインします。</p>

      {error ? (
        <p className="mt-6 rounded border border-rose-900 bg-rose-950/40 px-3 py-2 text-sm text-rose-200">
          {error}
        </p>
      ) : null}

      <form action={signInAction} className="mt-6">
        <input name="next" type="hidden" value={next || "/"} />
        <input name="origin" type="hidden" value={origin} />
        <button
          className="w-full rounded bg-neutral-100 px-4 py-2 text-sm font-medium text-neutral-950 hover:bg-white"
          type="submit"
        >
          OAuth でサインイン
        </button>
      </form>
    </main>
  );
}
