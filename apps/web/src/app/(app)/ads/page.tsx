"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  CheckCircle2,
  Loader2,
  Megaphone,
  Play,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { ApiClientError } from "@/lib/api-client";
import {
  adsApi,
  type AdAccountRecord,
  type AdCampaignSnapshotRecord,
  type AdDraftRecord,
  type CapiEventReceipt,
} from "@/lib/ads-api";
import { metaApi, type MetaConnectedAssetRecord } from "@/lib/meta-api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Alert, Badge } from "@/components/ui/badge";

function formatUsd(cents: number | null | undefined): string {
  if (cents == null) return "—";
  return `$${(cents / 100).toFixed(2)}`;
}

export default function AdsConsolePage() {
  const { activeWorkspace } = useAuth();
  const [accounts, setAccounts] = useState<AdAccountRecord[]>([]);
  const [campaigns, setCampaigns] = useState<AdCampaignSnapshotRecord[]>([]);
  const [drafts, setDrafts] = useState<AdDraftRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [draftName, setDraftName] = useState("Q4 Approved Reels Launch");
  const [draftBudgetUsd, setDraftBudgetUsd] = useState("50");
  const [selectedAccountId, setSelectedAccountId] = useState("");
  const [prompt, setPrompt] = useState(
    "Launch a traffic campaign for our reels editing service targeting US creators aged 21-45 with a $50 daily budget."
  );
  const [pageAssets, setPageAssets] = useState<MetaConnectedAssetRecord[]>([]);
  const [selectedPageAssetId, setSelectedPageAssetId] = useState("");
  const [pixelInput, setPixelInput] = useState("");
  const [capiEventName, setCapiEventName] = useState("Lead");
  const [capiEmail, setCapiEmail] = useState("");
  const [capiTestCode, setCapiTestCode] = useState("TEST12345");
  const [capiReceipt, setCapiReceipt] = useState<CapiEventReceipt | null>(null);

  const loadData = useCallback(async () => {
    if (!activeWorkspace) return;
    setLoading(true);
    setError(null);
    try {
      const [acctList, campList, draftList, connList] = await Promise.all([
        adsApi.listAccounts(activeWorkspace.workspace_id),
        adsApi.listCampaigns(activeWorkspace.workspace_id),
        adsApi.listDrafts(activeWorkspace.workspace_id),
        metaApi.listConnections(activeWorkspace.workspace_id),
      ]);
      setAccounts(acctList);
      setCampaigns(campList);
      setDrafts(draftList);
      if (acctList.length > 0 && !selectedAccountId) {
        setSelectedAccountId(acctList[0].id);
      }
      const pageLists = await Promise.all(
        connList.map((c) => metaApi.listAssets(activeWorkspace.workspace_id, c.id, false))
      );
      const pages = pageLists
        .flat()
        .filter((a) => a.asset_type === "facebook_page" && a.is_selected);
      setPageAssets(pages);
      if (pages.length > 0 && !selectedPageAssetId) {
        setSelectedPageAssetId(pages[0].id);
      }
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Failed to load Ads console.");
    } finally {
      setLoading(false);
    }
  }, [activeWorkspace, selectedAccountId, selectedPageAssetId]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  async function handleSyncAds() {
    if (!activeWorkspace) return;
    setSyncing(true);
    setError(null);
    setNotice(null);
    try {
      const res = await adsApi.syncAds(activeWorkspace.workspace_id);
      setNotice(
        `Synced ${res.accounts_synced} ad account(s) and ${res.campaigns_synced} campaign snapshot(s).`
      );
      await loadData();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Ad account sync failed.");
    } finally {
      setSyncing(false);
    }
  }

  async function handleCreateDraft(e: React.FormEvent) {
    e.preventDefault();
    if (!activeWorkspace || !selectedAccountId) return;
    setBusyId("create");
    setError(null);
    setNotice(null);
    try {
      const budgetCents = Math.max(100, Math.round(Number(draftBudgetUsd || "25") * 100));
      await adsApi.createDraft(activeWorkspace.workspace_id, {
        ad_account_id: selectedAccountId,
        name: draftName.trim(),
        objective: "OUTCOME_AWARENESS",
        daily_budget_cents: budgetCents,
        targeting: { countries: ["US"], age_min: 21, age_max: 45 },
        creative_refs: ["approved-reel-v1"],
      });
      setNotice("Created campaign draft (zero spend until validated and explicitly approved).");
      await loadData();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Draft creation failed.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleValidate(draftId: string) {
    if (!activeWorkspace) return;
    setBusyId(draftId);
    setError(null);
    try {
      const updated = await adsApi.validateDraft(activeWorkspace.workspace_id, draftId);
      setNotice(
        updated.validation_passed
          ? `Draft v${updated.version_number} passed validation.`
          : `Draft failed validation: ${(updated.validation_report.errors ?? []).join("; ")}`
      );
      await loadData();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Validation failed.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleRequestApproval(draftId: string) {
    if (!activeWorkspace) return;
    setBusyId(draftId);
    setError(null);
    try {
      const res = await adsApi.requestDraftApproval(activeWorkspace.workspace_id, draftId);
      setNotice(`Requested explicit campaign_execution approval (${res.approval_id}).`);
      await loadData();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Approval request failed.");
    } finally {
      setBusyId(null);
    }
  }

  async function handlePromptDraft(e: React.FormEvent) {
    e.preventDefault();
    if (!activeWorkspace || !selectedAccountId) return;
    setBusyId("prompt");
    setError(null);
    setNotice(null);
    try {
      const draft = await adsApi.createPromptDraft(activeWorkspace.workspace_id, {
        ad_account_id: selectedAccountId,
        page_asset_id: selectedPageAssetId || null,
        prompt: prompt.trim(),
      });
      setNotice(
        draft.validation_passed
          ? `Prompt draft "${draft.name}" created and validated. Request approval to execute.`
          : `Prompt draft created but needs attention: ${(draft.validation_report.errors ?? []).join("; ")}`
      );
      await loadData();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Prompt draft failed.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleSavePixel(e: React.FormEvent) {
    e.preventDefault();
    if (!activeWorkspace || !selectedAccountId) return;
    setBusyId("pixel");
    setError(null);
    setNotice(null);
    try {
      const updated = await adsApi.updatePixel(
        activeWorkspace.workspace_id,
        selectedAccountId,
        pixelInput.trim() || null
      );
      setNotice(
        updated.pixel_dataset_id
          ? `Bound pixel ${updated.pixel_dataset_id} to ${updated.name}.`
          : `Cleared pixel binding on ${updated.name}.`
      );
      setPixelInput("");
      await loadData();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Pixel update failed.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleCapiTest(e: React.FormEvent) {
    e.preventDefault();
    if (!activeWorkspace || !selectedAccountId) return;
    setBusyId("capi");
    setError(null);
    setNotice(null);
    setCapiReceipt(null);
    try {
      const receipt = await adsApi.sendCapiEvent(activeWorkspace.workspace_id, {
        ad_account_id: selectedAccountId,
        event_name: capiEventName.trim() || "Lead",
        email: capiEmail.trim() || undefined,
        test_event_code: capiTestCode.trim() || undefined,
      });
      setCapiReceipt(receipt);
      setNotice(
        receipt.test_mode
          ? `CAPI test event accepted (${receipt.events_received} received, trace ${receipt.fbtrace_id ?? "n/a"}).`
          : `CAPI live event sent (${receipt.events_received} received).`
      );
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "CAPI event failed.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleExecute(draftId: string) {
    if (!activeWorkspace) return;
    setBusyId(draftId);
    setError(null);
    try {
      const idemKey =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `ads-${Date.now()}`;
      const res = await adsApi.executeDraft(activeWorkspace.workspace_id, draftId, idemKey);
      setNotice(
        `Executed approved campaign draft (remote ID: ${res.draft.remote_campaign_id ?? "created"}).`
      );
      await loadData();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Execution blocked or failed.");
    } finally {
      setBusyId(null);
    }
  }

  if (!activeWorkspace) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Create a workspace first</CardTitle>
          <CardDescription>
            Meta Ads read-only reporting and approval-gated execution require an active workspace.
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
          <h1 className="text-2xl font-semibold tracking-tight">Ads Read & Controlled Execution</h1>
          <p className="text-sm text-muted-foreground">
            Strictly separated from organic publishing. Zero automatic spend increases; every campaign or budget mutation requires explicit human approval and append-only audit logging.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => void handleSyncAds()} disabled={syncing}>
            {syncing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Megaphone className="h-4 w-4" />}
            Sync Ad Accounts
          </Button>
          <Button variant="outline" size="sm" onClick={() => void loadData()} disabled={loading}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Refresh
          </Button>
        </div>
      </div>

      {error && <Alert variant="destructive">{error}</Alert>}
      {notice && <Alert variant="default">{notice}</Alert>}

      {/* Safety Banner */}
      <Card className="border-indigo-500/30 bg-indigo-950/20">
        <CardContent className="flex items-start gap-3 py-4 text-xs text-muted-foreground">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-indigo-400" />
          <p>
            <strong className="text-foreground">Ads Spend Safety Gate:</strong> Ad discovery uses
            read-only <code className="text-foreground">ads_read</code>. Campaign execution and
            budget mutations require <code className="text-foreground">ads_management</code>, a
            version-bound human approval, explicit confirmation, remote-state verification on
            timeouts, and immutable before/after audit logging.
          </p>
        </CardContent>
      </Card>

      {/* Connected Ad Accounts */}
      <div className="grid gap-3 sm:grid-cols-3">
        {accounts.length === 0 ? (
          <Card className="sm:col-span-3">
            <CardContent className="py-5 text-sm text-muted-foreground">
              No synced ad accounts yet. Connect a Meta Business Portfolio with{" "}
              <code>ads_read</code> on the{" "}
              <Link href="/connections" className="text-indigo-400 underline">
                Connections
              </Link>{" "}
              page, then click <strong>Sync Ad Accounts</strong>.
            </CardContent>
          </Card>
        ) : (
          accounts.map((acct) => (
            <Card key={acct.id}>
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between gap-2">
                  <CardTitle className="text-sm">{acct.name}</CardTitle>
                  <Badge variant="success">{acct.account_status}</Badge>
                </div>
                <CardDescription>
                  <code>{acct.external_account_id}</code> · {acct.currency}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-1 text-xs text-muted-foreground">
                <p>Amount Spent: {formatUsd(acct.amount_spent_cents)}</p>
                <p>Spend Cap: {formatUsd(acct.spend_cap_cents)}</p>
                <p>Pixel: {acct.pixel_dataset_id ?? "not bound"}</p>
              </CardContent>
            </Card>
          ))
        )}
      </div>

      {/* Campaign -> Ad Set -> Ad Hierarchy */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Campaign Hierarchy ({campaigns.length})</CardTitle>
          <CardDescription>Read-only campaign, ad set, and ad performance snapshots.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {campaigns.length === 0 ? (
            <p className="text-sm text-muted-foreground">No campaign snapshots synced yet.</p>
          ) : (
            campaigns.map((camp) => (
              <div key={camp.id} className="space-y-2 rounded-lg border border-border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-sm font-medium text-foreground">{camp.name}</p>
                    <p className="text-xs text-muted-foreground">
                      <code>{camp.external_campaign_id}</code> · {camp.objective} · Daily Budget:{" "}
                      {formatUsd(camp.daily_budget_cents)}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant="outline">{camp.status}</Badge>
                    <Badge variant="secondary">Spend {formatUsd(camp.spend_cents)}</Badge>
                    <Badge variant="secondary">{camp.impressions.toLocaleString()} impr</Badge>
                  </div>
                </div>
                {camp.ad_sets.length > 0 && (
                  <div className="space-y-1.5 border-l-2 border-border pl-3">
                    {camp.ad_sets.map((adSet) => (
                      <div key={adSet.id} className="text-xs text-muted-foreground">
                        <span className="font-medium text-foreground">{adSet.name}</span> (
                        {adSet.optimization_goal}, {adSet.clicks} clicks, {adSet.ads.length} ad(s))
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))
          )}
        </CardContent>
      </Card>

      {/* Single-Prompt Campaign Builder */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Single-Prompt Campaign Builder</CardTitle>
          <CardDescription>
            Describe the campaign once; Metuse drafts structure, budget, targeting, and
            creative brief. Still zero spend until validated and explicitly approved.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {accounts.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Sync an ad account first to enable prompt drafting.
            </p>
          ) : (
            <form onSubmit={(e) => void handlePromptDraft(e)} className="space-y-2">
              <div className="flex flex-wrap gap-2">
                <select
                  aria-label="Ad account"
                  value={selectedAccountId}
                  onChange={(e) => setSelectedAccountId(e.target.value)}
                  className="rounded-md border border-border bg-background px-2.5 py-1.5 text-xs"
                >
                  {accounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="Facebook Page"
                  value={selectedPageAssetId}
                  onChange={(e) => setSelectedPageAssetId(e.target.value)}
                  className="rounded-md border border-border bg-background px-2.5 py-1.5 text-xs"
                >
                  <option value="">No Page attached</option>
                  {pageAssets.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.display_name}
                    </option>
                  ))}
                </select>
              </div>
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                rows={3}
                maxLength={2000}
                placeholder="Describe the campaign goal, audience, and budget…"
                className="w-full rounded-md border border-border bg-background px-2.5 py-1.5 text-xs"
              />
              <Button type="submit" size="sm" disabled={busyId === "prompt"}>
                {busyId === "prompt" ? "Drafting…" : "Generate Draft from Prompt"}
              </Button>
            </form>
          )}
        </CardContent>
      </Card>

      {/* Campaign Drafts & Approval-Gated Execution */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Campaign Drafts ({drafts.length})</CardTitle>
          <CardDescription>
            Prepare and validate drafts safely. Execution stays blocked until explicitly approved.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {accounts.length > 0 && (
            <form onSubmit={(e) => void handleCreateDraft(e)} className="flex flex-wrap gap-2">
              <select
                aria-label="Ad account"
                value={selectedAccountId}
                onChange={(e) => setSelectedAccountId(e.target.value)}
                className="rounded-md border border-border bg-background px-2.5 py-1.5 text-xs"
              >
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
              <input
                type="text"
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                placeholder="Campaign draft name"
                className="min-w-[200px] flex-1 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs"
              />
              <input
                type="number"
                min={1}
                step={1}
                value={draftBudgetUsd}
                onChange={(e) => setDraftBudgetUsd(e.target.value)}
                aria-label="Daily budget USD"
                className="w-28 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs"
              />
              <Button type="submit" size="sm" disabled={busyId === "create"}>
                Create Draft
              </Button>
            </form>
          )}

          {drafts.length === 0 ? (
            <p className="text-sm text-muted-foreground">No campaign drafts created yet.</p>
          ) : (
            drafts.map((draft) => (
              <div
                key={draft.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border p-3"
              >
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-foreground">{draft.name}</span>
                    <Badge variant={draft.status === "executed" ? "success" : "secondary"}>
                      {draft.status}
                    </Badge>
                    <Badge variant="outline">v{draft.version_number}</Badge>
                    {draft.validation_passed && <Badge variant="success">Validated</Badge>}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Daily Budget: {formatUsd(draft.daily_budget_cents)} · Objective: {draft.objective}
                    {draft.remote_campaign_id ? ` · Remote: ${draft.remote_campaign_id}` : ""}
                  </p>
                </div>
                {draft.status !== "executed" && (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busyId === draft.id}
                      onClick={() => void handleValidate(draft.id)}
                    >
                      <CheckCircle2 className="h-3.5 w-3.5" />
                      Validate
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busyId === draft.id || !draft.validation_passed}
                      onClick={() => void handleRequestApproval(draft.id)}
                    >
                      <ShieldCheck className="h-3.5 w-3.5" />
                      Request Approval
                    </Button>
                    <Button
                      size="sm"
                      disabled={busyId === draft.id}
                      onClick={() => void handleExecute(draft.id)}
                    >
                      <Play className="h-3.5 w-3.5" />
                      Execute
                    </Button>
                  </div>
                )}
              </div>
            ))
          )}
        </CardContent>
      </Card>

      {/* Conversions API */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Conversions API</CardTitle>
          <CardDescription>
            Bind a pixel to an ad account, then send hashed server events. Test codes
            stay in test mode; live sends require execution rights.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {accounts.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Sync an ad account first to enable Conversions API.
            </p>
          ) : (
            <>
              <form onSubmit={(e) => void handleSavePixel(e)} className="flex flex-wrap gap-2">
                <input
                  type="text"
                  inputMode="numeric"
                  value={pixelInput}
                  onChange={(e) => setPixelInput(e.target.value)}
                  placeholder="Pixel / dataset ID (empty clears)"
                  className="min-w-[220px] flex-1 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs"
                />
                <Button type="submit" size="sm" disabled={busyId === "pixel"}>
                  Save Pixel
                </Button>
              </form>
              <form onSubmit={(e) => void handleCapiTest(e)} className="flex flex-wrap gap-2">
                <input
                  type="text"
                  value={capiEventName}
                  onChange={(e) => setCapiEventName(e.target.value)}
                  placeholder="Event name (Lead, Purchase…)"
                  aria-label="Event name"
                  className="w-40 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs"
                />
                <input
                  type="email"
                  value={capiEmail}
                  onChange={(e) => setCapiEmail(e.target.value)}
                  placeholder="User email (hashed server-side)"
                  aria-label="User email"
                  className="min-w-[200px] flex-1 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs"
                />
                <input
                  type="text"
                  value={capiTestCode}
                  onChange={(e) => setCapiTestCode(e.target.value)}
                  placeholder="Test event code (empty = live)"
                  aria-label="Test event code"
                  className="w-44 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs"
                />
                <Button type="submit" size="sm" disabled={busyId === "capi"}>
                  {busyId === "capi" ? "Sending…" : "Send Event"}
                </Button>
              </form>
              {capiReceipt && (
                <p className="text-xs text-muted-foreground">
                  Receipt: {capiReceipt.event_name} → pixel {capiReceipt.pixel_dataset_id} ·{" "}
                  {capiReceipt.test_mode ? "test mode" : "LIVE"} · received{" "}
                  {capiReceipt.events_received} · id {capiReceipt.event_id}
                </p>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
