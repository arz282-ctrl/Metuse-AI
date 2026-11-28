"use client";

import React, { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { ApiClientError } from "@/lib/api-client";
import { metaApi } from "@/lib/meta-api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert } from "@/components/ui/badge";

function decodeWorkspaceId(state: string): string | null {
  try {
    const payload = state.split(".")[1] ?? "";
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    const claims = JSON.parse(json) as { ws?: unknown };
    return typeof claims.ws === "string" && claims.ws.length > 0 ? claims.ws : null;
  } catch {
    return null;
  }
}

function ConnectionsCallbackInner() {
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const code = params.get("code");
    const state = params.get("state");
    const providerError = params.get("error_description") || params.get("error");
    if (providerError || !code || !state) {
      setError(providerError || "Missing authorization code from Meta.");
      return;
    }
    const workspaceId = decodeWorkspaceId(state);
    if (!workspaceId) {
      setError("Meta login state is invalid. Please reconnect from the Connections page.");
      return;
    }
    (async () => {
      try {
        const redirectUri = `${window.location.origin}/connections/callback`;
        await metaApi.completeUnifiedLogin(workspaceId, { code, state, redirect_uri: redirectUri });
        router.replace("/connections");
      } catch (err) {
        setError(err instanceof ApiClientError ? err.message : "Meta connection failed.");
      }
    })();
  }, [params, router]);

  return (
    <Card className="mx-auto w-full max-w-md">
      <CardHeader>
        <CardTitle>Completing Meta connection…</CardTitle>
        <CardDescription>Exchanging the authorization code server-side.</CardDescription>
      </CardHeader>
      <CardContent>
        {error ? (
          <div className="space-y-3">
            <Alert variant="destructive">{error}</Alert>
            <Link href="/connections" className="text-sm font-medium text-indigo-400 hover:underline">
              Back to Connections
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
  );
}

export default function ConnectionsCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading…
        </div>
      }
    >
      <ConnectionsCallbackInner />
    </Suspense>
  );
}
