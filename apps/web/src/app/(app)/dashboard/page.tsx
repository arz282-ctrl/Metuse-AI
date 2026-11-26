"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { Building2, Clock, Loader2, PlusCircle, ShieldCheck, Users } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { apiClient, ApiClientError } from "@/lib/api-client";
import type { AuditLogRecord, ClientRecord } from "@/lib/types";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Alert, Badge } from "@/components/ui/badge";

export default function DashboardPage() {
  const { user, workspaces, activeWorkspace } = useAuth();
  const [clients, setClients] = useState<ClientRecord[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLogRecord[]>([]);
  const [loadingData, setLoadingData] = useState(false);
  const [dataError, setDataError] = useState<string | null>(null);

  useEffect(() => {
    if (!activeWorkspace) {
      setClients([]);
      setAuditLogs([]);
      return;
    }
    let cancelled = false;
    async function load() {
      setLoadingData(true);
      setDataError(null);
      try {
        const [clientList, auditRes] = await Promise.all([
          apiClient.listClients(activeWorkspace!.workspace_id),
          apiClient.listAuditLogs(activeWorkspace!.workspace_id, 8),
        ]);
        if (!cancelled) {
          setClients(clientList);
          setAuditLogs(auditRes.items);
        }
      } catch (err) {
        if (!cancelled) {
          setDataError(
            err instanceof ApiClientError ? err.message : "Failed to load workspace overview."
          );
        }
      } finally {
        if (!cancelled) setLoadingData(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [activeWorkspace]);

  if (!activeWorkspace) {
    return (
      <div className="mx-auto max-w-2xl py-8">
        <Card>
          <CardHeader>
            <CardTitle>Welcome to Metuse AI, {user?.full_name || user?.email}</CardTitle>
            <CardDescription>
              Create your first Agency or Creator workspace to unlock client onboarding and tenant-isolated operations.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link href="/onboarding/workspace">
              <Button>
                <Building2 className="h-4 w-4" />
                Create your first workspace
              </Button>
            </Link>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{activeWorkspace.workspace_name}</h1>
          <p className="text-sm text-muted-foreground">
            Phase 1 cloud foundation · Role: <span className="text-foreground">{activeWorkspace.role_name}</span>
          </p>
        </div>
        <div className="flex gap-2">
          <Link href="/onboarding/client">
            <Button size="sm">
              <PlusCircle className="h-4 w-4" />
              Onboard Client
            </Button>
          </Link>
          <Link href="/onboarding/workspace">
            <Button size="sm" variant="outline">
              New Workspace
            </Button>
          </Link>
        </div>
      </div>

      {dataError && <Alert variant="destructive">{dataError}</Alert>}

      <div className="grid gap-4 sm:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center justify-between">
              <span>Workspaces</span>
              <Building2 className="h-4 w-4 text-indigo-400" />
            </CardDescription>
            <CardTitle className="text-2xl">{workspaces.length}</CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            Active memberships scoped via PostgreSQL RLS
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center justify-between">
              <span>Clients</span>
              <Users className="h-4 w-4 text-indigo-400" />
            </CardDescription>
            <CardTitle className="text-2xl">{loadingData ? "—" : clients.length}</CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            Isolated to {activeWorkspace.workspace_name}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center justify-between">
              <span>Security Posture</span>
              <ShieldCheck className="h-4 w-4 text-emerald-400" />
            </CardDescription>
            <CardTitle className="text-2xl">FORCE RLS</CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            Immutable audit trail · {user?.auth_source}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <div>
              <CardTitle className="text-base">Clients</CardTitle>
              <CardDescription>Accounts managed in this workspace</CardDescription>
            </div>
            <Link href="/clients" className="text-xs font-medium text-indigo-400 hover:underline">
              View all
            </Link>
          </CardHeader>
          <CardContent>
            {loadingData ? (
              <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading clients...
              </div>
            ) : clients.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border p-6 text-center">
                <p className="text-sm text-muted-foreground">No clients onboarded yet.</p>
                <Link href="/onboarding/client" className="mt-3 inline-block">
                  <Button size="sm" variant="secondary">
                    Add first client
                  </Button>
                </Link>
              </div>
            ) : (
              <ul className="divide-y divide-border">
                {clients.slice(0, 5).map((client) => (
                  <li key={client.id} className="flex items-center justify-between py-2.5 text-sm">
                    <div>
                      <p className="font-medium">{client.name}</p>
                      <p className="text-xs text-muted-foreground">{client.slug}</p>
                    </div>
                    <Badge variant={client.status === "active" ? "success" : "secondary"}>
                      {client.status}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Recent Audit Activity</CardTitle>
            <CardDescription>Append-only workspace event log</CardDescription>
          </CardHeader>
          <CardContent>
            {loadingData ? (
              <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading audit trail...
              </div>
            ) : auditLogs.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                No workspace audit events recorded yet.
              </p>
            ) : (
              <ul className="divide-y divide-border">
                {auditLogs.map((log) => (
                  <li key={log.id} className="flex items-center justify-between py-2.5 text-xs">
                    <div className="space-y-0.5">
                      <p className="font-mono font-medium text-foreground">{log.action}</p>
                      <p className="text-muted-foreground">target: {log.target_type}</p>
                    </div>
                    <span className="flex items-center gap-1 text-muted-foreground">
                      <Clock className="h-3 w-3" />
                      {new Date(log.created_at).toLocaleTimeString()}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
