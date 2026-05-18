"use server";

import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { getServerClient } from "@/lib/supabase/server";

type OAuthProvider = "github" | "google";

function getOAuthProvider(): OAuthProvider {
  const provider = process.env.NEXT_PUBLIC_SUPABASE_AUTH_PROVIDER;
  return provider === "google" ? "google" : "github";
}

function normalizeNext(value: FormDataEntryValue | null): string {
  const next = String(value || "/");
  if (!next.startsWith("/") || next.startsWith("//")) return "/";
  return next;
}

async function getRequestOrigin(): Promise<string> {
  const headerStore = await headers();
  const host = headerStore.get("x-forwarded-host") ?? headerStore.get("host") ?? "";
  const protocol = headerStore.get("x-forwarded-proto") ?? "http";
  return host ? `${protocol}://${host}` : "";
}

export async function signInAction(formData: FormData) {
  const next = normalizeNext(formData.get("next"));
  const origin = await getRequestOrigin();
  const supabase = await getServerClient();
  const { data, error } = await supabase.auth.signInWithOAuth({
    provider: getOAuthProvider(),
    options: {
      redirectTo: `${origin}/auth/callback?next=${encodeURIComponent(next)}`,
    },
  });

  if (error || !data.url) {
    redirect(`/login?error=${encodeURIComponent(error?.message || "OAuth sign-in failed")}`);
  }

  redirect(data.url);
}

export async function signOutAction() {
  const supabase = await getServerClient();
  await supabase.auth.signOut();
  redirect("/login");
}
