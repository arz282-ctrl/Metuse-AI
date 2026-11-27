"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Link2, Loader2, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { ApiClientError, apiClient } from "@/lib/api-client";
import type { ClientRecord } from "@/lib/types";
import {
  metaApi,
  type MetaConnectionRecord,
  type MetaConnectionType,
  type PermissionMatrixEntry,
} from "@/lib/meta-api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/input";
import { Alert, Badge } from "@/components/ui/badge";

export default function MetaConnectionsPage() {
  const { activeWorkspace } = useAuth();
  const [clients, setClients] = useState<ClientRecord[]>([]);
  const [matrix, setMatrix] = useState<PermissionMatrixEntry[]>([]);
  const [connections, setConnections] = useState<MetaConnectionRecord[]>([]);

  const [connectionType, setConnectionType] = useState<MetaConnectionType>("creator_professional");
  const [selectedClientId, setSelectedClientId] = useState("");
  const [includeAds, setIncludeAds] = useState(false);

  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    if (!activeWorkspace) return;
    setLoading(true);
    setError(null);
    try {
      const [clientList, permMatrix, connList] = await Promise.all([
        apiClient.listClients(activeWorkspace.workspace_id),
        metaApi.getPermissionMatrix(activeWorkspace.workspace_id),
        metaApi.listConnections(activeWorkspace.workspace_id),
      ]);
      setClients(clientList);
      setMatrix(permMatrix);
      setConnections(connList);
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Failed to load Meta connections.");
    } finally {
      setLoading(false);
    }
  }, [activeWorkspace]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  async function handleConnect(mockScenarioCode?: string) {
    if (!activeWorkspace) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const start = await metaApi.startUnifiedLogin(activeWorkspace.workspace_id, {
        connection_type: connectionType,
        client_id: selectedClientId || null,
        include_ads: includeAds,
      });

      if (start.mode === "live" && !mockScenarioCode) {
        window.location.assign(start.authorization_url);
        return;
      }

      const code =
        mockScenarioCode ||
        (connectionType === "agency_business" ? "mock_agency_biz" : "mock_creator_pro");
      const created = await metaApi.completeUnifiedLogin(activeWorkspace.workspace_id, {
        code,
        state: start.state,
        redirect_uri: start.redirect_uri,
      });
      setNotice(
        `Connected ${created.connection_type} (${created.instagram_account_ids.length} Instagram Professional account(s)).`
      );
      await loadData();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Meta connection failed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDisconnect(connectionId: string) {
    if (!activeWorkspace) return;
    setBusy(true);
    setError(null);
    try {
      await metaApi.disconnect(activeWorkspace.workspace_id, connectionId);
      setNotice("Meta connection disconnected and token removed from server secret vault.");
      await loadData();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Failed to disconnect.");
    } finally {
      setBusy(false);
    }
  }

  if (!activeWorkspace) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Create a workspace first</CardTitle>
          <CardDescription>
            Meta account connections bind strictly to a tenant-isolated workspace or client.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Link href="/onboarding/workspace">
            <Button>Create workspace</Button>
          </Link>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Meta Account Connections</h1>
          <p className="text-sm text-muted-foreground">
            Instagram API with Unified Login · Server-side encrypted secret references · Zero raw tokens in browser or Postgres.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void loadData()} disabled={loading}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Refresh
        </Button>
      </div>

      {error && <Alert variant="destructive">{error}</Alert>}
      {notice && <Alert variant="default">{notice}</Alert>}

      <div className="grid gap-6 lg:grid-cols-5">
        {/* Left: Onboarding Flow */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Link2 className="h-4 w-4 text-indigo-400" />
              Connect via Meta Unified Login
            </CardTitle>
            <CardDescription>
              Personal Instagram accounts are blocked with conversion instructions; Creators do not need an ad account.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-2">
              <Button
                type="button"
                size="sm"
                variant={connectionType === "creator_professional" ? "default" : "outline"}
                onClick={() => {
                  setConnectionType("creator_professional");
                  setIncludeAds(false);
                }}
              >
                Creator Pro
              </Button>
              <Button
                type="button"
                size="sm"
                variant={connectionType === "agency_business" ? "default" : "outline"}
                onClick={() => setConnectionType("agency_business")}
              >
                Agency Business
              </Button>
            </div>

            {connectionType === "agency_business" && (
              <div className="space-y-1.5">
                <Label htmlFor="meta-client">Client (optional)</Label>
                <select
                  id="meta-client"
                  value={selectedClientId}
                  onChange={(e) => setSelectedClientId(e.target.value)}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                >
                  <option value="">Workspace-level connection</option>
                  {clients.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </div>
            )}

            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={includeAds}
                onChange={(e) => setIncludeAds(e.target.checked)}
              />
              Request optional read-only Ad Account access (<code>ads_read</code>)
            </label>

            <div className="space-y-2">
              <Button
                type="button"
                className="w-full"
                disabled={busy}
                onClick={() => void handleConnect()}
              >
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
                Continue with Meta Hosted Login
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-full"
                disabled={busy}
                onClick={() => void handleConnect("mock_personal_ig")}
              >
                Test Personal IG Eligibility Check
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* Right: Active Connections & Capabilities */}
        <div className="space-y-6 lg:col-span-3">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Connected Accounts ({connections.length})</CardTitle>
              <CardDescription>
                Capabilities are computed server-side from granted scopes and eligible assets.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {connections.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No Meta accounts connected yet. You can still use AI Workers, Video Studio, and Cloud Studio without connecting Meta.
                </p>
              ) : (
                connections.map((conn) => (
                  <div key={conn.id} className="space-y-2 rounded-lg border border-border p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <Badge variant={conn.status === "active" ? "success" : "outline"}>
                          {conn.status}
                        </Badge>
                        <Badge variant="secondary">{conn.connection_type}</Badge>
                        {conn.token_configured && (
                          <Badge variant="outline" className="text-[11px]">
                            <ShieldCheck className="mr-1 h-3 w-3" />
                            Vault Secret Ref
                          </Badge>
                        )}
                      </div>
                      {conn.status !== "disconnected" && (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busy}
                          onClick={() => void handleDisconnect(conn.id)}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                          Disconnect
                        </Button>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      IG Accounts: {conn.instagram_account_ids.join(", ") || "none"} · Pages:{" "}
                      {conn.page_ids.join(", ") || "none"} · Ad Accounts:{" "}
                      {conn.ad_account_ids.join(", ") || "none"}
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {Object.entries(conn.capabilities).map(([cap, enabled]) => (
                        <Badge
                          key={cap}
                          variant={enabled ? "success" : "outline"}
                          className="text-[10px]"
                        >
                          {cap}: {enabled ? "on" : "off"}
                        </Badge>
                      ))}
                    </div>
                  </div>
                ))
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Permission Matrix (Section 2.3)</CardTitle>
              <CardDescription>
                Metuse requests only minimal permissions required for enabled features.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {matrix.map((row) => (
                <div
                  key={row.permission}
                  className="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 pb-2 text-xs last:border-b-0"
                >
                  <div>
                    <p className="font-medium text-foreground">
                      <code>{row.permission}</code> ({row.capability})
                    </p>
                    <p className="text-muted-foreground">{row.description}</p>
                  </div>
                  <Badge variant="outline" className="text-[10px]">
                    {row.required_for_creator_organic ? "Organic" : "Optional / Ads"}
                  </Badge>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
