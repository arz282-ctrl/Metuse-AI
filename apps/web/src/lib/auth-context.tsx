"use client";

import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { apiClient, ApiClientError, setStoredAccessToken } from "@/lib/api-client";
import type { AuthUser, MembershipSummary } from "@/lib/types";

const ACTIVE_WS_KEY = "metuse.active_workspace_id";

interface AuthContextValue {
  user: AuthUser | null;
  workspaces: MembershipSummary[];
  activeWorkspace: MembershipSummary | null;
  loading: boolean;
  error: string | null;
  setActiveWorkspaceId: (workspaceId: string) => void;
  refreshSession: () => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [workspaces, setWorkspaces] = useState<MembershipSummary[]>([]);
  const [activeWorkspaceId, setActiveWorkspaceIdState] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const setActiveWorkspaceId = useCallback((workspaceId: string) => {
    setActiveWorkspaceIdState(workspaceId);
    try {
      window.localStorage.setItem(ACTIVE_WS_KEY, workspaceId);
    } catch {
      // ignore storage errors
    }
  }, []);

  const refreshSession = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const currentUser = await apiClient.me();
      setUser(currentUser);
      const wsList = await apiClient.listWorkspaces();
      setWorkspaces(wsList);

      const savedId =
        typeof window !== "undefined" ? window.localStorage.getItem(ACTIVE_WS_KEY) : null;
      const validSaved = wsList.find((w) => w.workspace_id === savedId);
      if (validSaved) {
        setActiveWorkspaceIdState(validSaved.workspace_id);
      } else if (wsList.length > 0) {
        setActiveWorkspaceId(wsList[0].workspace_id);
      } else {
        setActiveWorkspaceIdState(null);
      }
    } catch (err) {
      if (err instanceof ApiClientError && err.status === 401) {
        setStoredAccessToken(null);
        setUser(null);
        setWorkspaces([]);
        setActiveWorkspaceIdState(null);
      } else {
        setUser(null);
        setWorkspaces([]);
        setError(err instanceof Error ? err.message : "Failed to load session");
      }
    } finally {
      setLoading(false);
    }
  }, [setActiveWorkspaceId]);

  useEffect(() => {
    void refreshSession();
  }, [refreshSession]);

  const signOut = useCallback(async () => {
    await apiClient.logout();
    setUser(null);
    setWorkspaces([]);
    setActiveWorkspaceIdState(null);
  }, []);

  const activeWorkspace =
    workspaces.find((w) => w.workspace_id === activeWorkspaceId) || workspaces[0] || null;

  return (
    <AuthContext.Provider
      value={{
        user,
        workspaces,
        activeWorkspace,
        loading,
        error,
        setActiveWorkspaceId,
        refreshSession,
        signOut,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
