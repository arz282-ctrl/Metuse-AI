"use client";

import React, { useState } from "react";
import { Loader2 } from "lucide-react";
import { apiClient, ApiClientError } from "@/lib/api-client";
import type { SocialProvider } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/badge";

const PROVIDER_LABEL: Record<SocialProvider, string> = {
  meta: "Continue with Meta",
  google: "Continue with Google",
};

export function SocialLoginButtons({ mode }: { mode: "signin" | "signup" }) {
  const [busy, setBusy] = useState<SocialProvider | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleStart(provider: SocialProvider) {
    setBusy(provider);
    setError(null);
    try {
      const start = await apiClient.socialStart(provider);
      window.location.assign(start.authorization_url);
    } catch (err) {
      setError(
        err instanceof ApiClientError
          ? err.message
          : `${PROVIDER_LABEL[provider]} failed to start.`
      );
      setBusy(null);
    }
  }

  return (
    <div className="space-y-2">
      {error && <Alert variant="destructive">{error}</Alert>}
      {(Object.keys(PROVIDER_LABEL) as SocialProvider[]).map((provider) => (
        <Button
          key={provider}
          type="button"
          variant="outline"
          className="w-full"
          disabled={busy !== null}
          onClick={() => void handleStart(provider)}
        >
          {busy === provider ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <span
              aria-hidden
              className="flex h-4 w-4 items-center justify-center rounded-full bg-indigo-500 text-[9px] font-bold text-white"
            >
              {provider === "meta" ? "M" : "G"}
            </span>
          )}
          {mode === "signup" ? PROVIDER_LABEL[provider].replace("Continue", "Sign up") : PROVIDER_LABEL[provider]}
        </Button>
      ))}
    </div>
  );
}
