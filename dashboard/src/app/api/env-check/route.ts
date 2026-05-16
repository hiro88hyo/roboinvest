import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export function GET() {
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";

  return NextResponse.json({
    nextPublicSupabaseUrl: Boolean(supabaseUrl),
    nextPublicSupabaseAnonKey: Boolean(process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY),
    supabaseSecretKey: Boolean(process.env.SUPABASE_SECRET_KEY),
    nextPublicAppTimezone: process.env.NEXT_PUBLIC_APP_TIMEZONE ?? null,
    supabaseHost: parseHost(supabaseUrl),
  });
}

function parseHost(value: string) {
  if (!value) {
    return null;
  }

  try {
    return new URL(value).host;
  } catch {
    return "invalid-url";
  }
}
