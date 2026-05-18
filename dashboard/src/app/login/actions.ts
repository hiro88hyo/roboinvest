"use server";

import { redirect } from "next/navigation";
import { getServerClient } from "@/lib/supabase/server";

type OAuthProvider = "github" | "google";

function getOAuthProvider(): OAuthProvider {
  const provider = process.env.NEXT_PUBLIC_SUPABASE_AUTH_PROVIDER;
  return provider === "google" ? "google" : "github";
}

export async function signInAction(formData: FormData) {
  const next = String(formData.get("next") || "/");
  const origin = String(formData.get("origin") || "");
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
