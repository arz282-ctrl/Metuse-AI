"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, PlusCircle, Users } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { apiClient, ApiClientError } from "@/lib/api-client";
import type { ClientRecord } from "@/lib/types";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Alert, Badge } from "@/components/ui/badge";

export default function ClientsPage() {
  const { activeWorkspace } = useAuth();
  const [clients, setClients] = useState<ClientRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!activeWorkspace) return;
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const rows = await apiClient.listClients(activeWorkspace!.workspace_id);
        if (!cancelled) setClients(rows);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiClientError ? err.message : "Failed to load clients.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [activeWorkspace]);

  return (
    <div className="space-y-6">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Clients</h1>
          <p className="text-sm text-muted-foreground">
            Tenant-scoped client accounts for {activeWorkspace?.workspace_name ?? "your workspace"}.
          </p>
        </div>
        <Link href="/onboarding/client">
          <Button size="sm">
            <PlusCircle className="h-4 w-4" />
            New Client
          </Button>
        </Link>
      </div>

      {error && <Alert variant="destructive">{error}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Client Directory</CardTitle>
          <CardDescription>Protected by PostgreSQL FORCE RLS and RBAC</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading clients...
            </div>
          ) : clients.length === 0 ? (
            <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border py-10 text-center">
              <Users className="h-8 w-8 text-muted-foreground" />
              <p className="mt-2 text-sm font-medium">No clients yet</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Onboard your first client to organize brand assets and workflows.
              </p>
              <Link href="/onboarding/client" className="mt-4">
                <Button size="sm">Onboard Client</Button>
              </Link>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-border text-xs uppercase text-muted-foreground">
                  <tr>
                    <th className="py-2.5 pr-4">Name</th>
                    <th className="py-2.5 pr-4">Slug</th>
                    <th className="py-2.5 pr-4">Status</th>
                    <th className="py-2.5">Created</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {clients.map((client) => (
                    <tr key={client.id}>
                      <td className="py-3 pr-4 font-medium">{client.name}</td>
                      <td className="py-3 pr-4 font-mono text-xs text-muted-foreground">{client.slug}</td>
                      <td className="py-3 pr-4">
                        <Badge variant={client.status === "active" ? "success" : "secondary"}>
                          {client.status}
                        </Badge>
                      </td>
                      <td className="py-3 text-xs text-muted-foreground">
                        {new Date(client.created_at).toLocaleDateString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
