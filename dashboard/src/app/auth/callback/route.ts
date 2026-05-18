import { redirect } from "next/navigation";
import { type NextRequest, NextResponse } from "next/server";
import { getServerClient } from "@/lib/supabase/server";

function normalizeNext(value: string | null): string {
  const next = value || "/";
  if (!next.startsWith("/") || next.startsWith("//")) return "/";
  return next;
}

export async function GET(request: NextRequest) {
  const requestUrl = new URL(request.url);
  const code = requestUrl.searchParams.get("code");
  const next = normalizeNext(requestUrl.searchParams.get("next"));

  if (!code) {
    redirect("/login?error=Missing OAuth callback code");
  }

  const supabase = await getServerClient();
  const { error } = await supabase.auth.exchangeCodeForSession(code);
  if (error) {
    redirect(`/login?error=${encodeURIComponent(error.message)}`);
  }

  return NextResponse.redirect(new URL(next, request.url));
}
