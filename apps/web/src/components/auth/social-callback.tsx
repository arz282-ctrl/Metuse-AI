"use client";

import React, { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { apiClient, ApiClientError } from "@/lib/api-client";
import { useAuth } from "@/lib/auth-context";
import type { SocialProvider } from "@/lib/types";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert } from "@/components/ui/badge";

function SocialCallbackInner({ provider }: { provider: SocialProvider }) {
  const router = useRouter();
  const params = useSearchParams();
  const { refreshSession, setActiveWorkspaceId } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const code = params.get("code");
    const state = params.get("state");
    const providerError = params.get("error_description") || params.get("error");
    if (providerError || !code || !state) {
      setError(providerError || "Missing authorization code from provider.");
      return;
    }
    (async () => {
      try {
        const res = await apiClient.socialCallback(provider, { code, state });
        await refreshSession();
        if (res.workspace_id) {
          setActiveWorkspaceId(res.workspace_id);
        }
        // New social users land on Connections to grant business asset access;
        // returning users go straight to the dashboard.
        router.replace(res.is_new_user ? "/connections" : "/dashboard");
      } catch (err) {
        setError(
          err instanceof ApiClientError ? err.message : "Social sign-in failed."
        );
      }
    })();
  }, [params, provider, refreshSession, router, setActiveWorkspaceId]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4 py-12">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Completing {provider === "meta" ? "Meta" : "Google"} sign-in…</CardTitle>
          <CardDescription>Exchanging the authorization code server-side.</CardDescription>
        </CardHeader>
        <CardContent>
          {error ? (
            <div className="space-y-3">
              <Alert variant="destructive">{error}</Alert>
              <Link href="/login" className="text-sm font-medium text-indigo-400 hover:underline">
                Back to sign in
              </Link>
            </div>
          ) : (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Verifying with Metuse API…
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export function SocialCallback({ provider }: { provider: SocialProvider }) {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-background px-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading…
          </div>
        </div>
      }
    >
      <SocialCallbackInner provider={provider} />
    </Suspense>
  );
}
