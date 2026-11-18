/**
 * Baton Production Authentication Context
 * Manages JWT session state, user profile (/me), demo role transitions,
 * and live synchronization with the FastAPI backend.
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  batonApi,
  getStoredToken,
  setStoredToken,
  type Role,
  type UserProfile,
  ApiError,
} from "../api/client";
import { supabase } from "../features/auth/supabase";

export interface ExtendedUser extends UserProfile {
  firstName: string;
  initials: string;
  email: string;
  displayRole: string;
  is_demo: boolean;
}

export interface RegisterParams {
  email: string;
  name: string;
  role: Role;
  invitation_code?: string;
  user_id?: string;
  supabase_token?: string;
}

export interface AuthContextType {
  user: ExtendedUser | null;
  loading: boolean;
  error: string | null;
  isAuthenticated: boolean;
  loginDemo: (role?: Role) => Promise<void>;
  register: (payload: RegisterParams) => Promise<ExtendedUser>;
  syncSession: (user_id: string, email: string, name?: string) => Promise<{ registered: boolean; requires_role?: boolean }>;
  switchRole: (role: Role) => Promise<void>;
  logout: () => Promise<void>;
  resetDemo: () => Promise<void>;
  refreshProfile: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

// Human-friendly role metadata for non-technical users
export const ROLE_DETAILS: Record<Role, { title: string; email: string; avatarText: string }> = {
  approver: {
    title: "Project Admin",
    email: "manjiri.relekar47@gmail.com",
    avatarText: "MJ",
  },
  leaver: {
    title: "Aarav (Departing Lead)",
    email: "aarav.lead@baton.internal",
    avatarText: "AR",
  },
  incoming_maintainer: {
    title: "Meera (Incoming Maintainer)",
    email: "meera.maintainer@baton.internal",
    avatarText: "MR",
  },
  incoming_contributor: {
    title: "Incoming Contributor",
    email: "contributor@baton.internal",
    avatarText: "IC",
  },
};

/**
 * Formats user's name first, followed by their assigned role in brackets for the account/profile dropdown.
 * E.g. "Aarav (Departing Lead)", "Meera (Incoming Maintainer)", "Samiksha (Engineering Lead)".
 * Fetches the name from the authenticated user's profile instead of using the email address.
 */
export function formatUserDropdownRole(
  user: { name?: string; role?: Role | string; displayRole?: string; email?: string } | null | undefined
): string {
  if (!user) return "Member";

  // Fetch name strictly from authenticated user's profile, not email
  const name = (user.name && user.name.trim()) || "Member";

  let roleTitle = "Member";
  if (user.role === "leaver") {
    roleTitle = "Departing Lead";
  } else if (user.role === "incoming_maintainer") {
    roleTitle = "Incoming Maintainer";
  } else if (user.role === "incoming_contributor") {
    roleTitle = "Incoming Contributor";
  } else if (user.role === "approver") {
    roleTitle = "Engineering Lead";
  } else if (user.displayRole) {
    roleTitle = user.displayRole.replace(/\s*\([^)]*\)$/, "").trim() || user.displayRole;
  }

  return `${name} (${roleTitle})`;
}

export function formatUser(
  rawUser: UserProfile,
  authenticatedEmail?: string | null,
): ExtendedUser {
  const meta = ROLE_DETAILS[rawUser.role] || {
    title: "Project Member",
    email: `${rawUser.id}@baton.internal`,
    avatarText: rawUser.name.slice(0, 2).toUpperCase(),
  };

  const nameParts = rawUser.name.trim().split(" ");
  const firstName = nameParts[0] || rawUser.name;
  const initials =
    nameParts.length >= 2
      ? `${nameParts[0][0]}${nameParts[1][0]}`.toUpperCase()
      : meta.avatarText;

  const is_demo =
    rawUser.is_demo !== undefined
      ? Boolean(rawUser.is_demo)
      : rawUser.id.startsWith("u-") &&
        ["approver", "leaver", "incoming_maintainer", "incoming_contributor"].includes(rawUser.role);

  let displayRole = meta.title;
  if (!is_demo && rawUser.name && rawUser.name.trim()) {
    displayRole = formatUserDropdownRole(rawUser);
  }

  return {
    ...rawUser,
    firstName,
    initials,
    email: authenticatedEmail || rawUser.email || meta.email,
    displayRole,
    is_demo,
  };
}

async function getAuthenticatedEmail(): Promise<string | undefined> {
  if (!supabase) return undefined;
  const { data } = await supabase.auth.getSession();
  return data.session?.user.email || undefined;
}

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<ExtendedUser | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  /**
   * Logs in using a demo role and saves the signed JWT
   */
  const loginDemo = useCallback(async (role: Role = "approver") => {
    setLoading(true);
    setError(null);
    try {
      const response = await batonApi.demoLogin(role);
      setStoredToken(response.token);
      setUser(formatUser(response.user, await getAuthenticatedEmail()));
    } catch (err: unknown) {
      const message = err instanceof ApiError ? err.message : "Failed to sign in via demo role";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  /**
   * Registers a new user with chosen role and saves the signed JWT
   */
  const register = useCallback(async (payload: RegisterParams) => {
    setLoading(true);
    setError(null);
    try {
      const activeSession = (await supabase?.auth.getSession())?.data.session;
      const token = payload.supabase_token || activeSession?.access_token;
      const response = await batonApi.register({ ...payload, supabase_token: token });
      setStoredToken(response.token);
      const authedEmail = await getAuthenticatedEmail();
      const formatted = formatUser(response.user, authedEmail || payload.email);
      setUser(formatted);
      return formatted;
    } catch (err: unknown) {
      const message = err instanceof ApiError ? err.message : "Registration failed";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  /**
   * Synchronizes an authenticated Supabase user with their assigned role in Baton
   */
  const syncSession = useCallback(async (user_id: string, email: string, name?: string) => {
    setLoading(true);
    setError(null);
    try {
      const activeSession = (await supabase?.auth.getSession())?.data.session;
      const token = activeSession?.access_token;
      const res = await batonApi.syncSession({ user_id, email, name, supabase_token: token });
      if (res.registered && res.token && res.user) {
        setStoredToken(res.token);
        const authedEmail = await getAuthenticatedEmail();
        setUser(formatUser(res.user, authedEmail || email));
        return { registered: true };
      }
      return { registered: false, requires_role: Boolean(res.requires_role) };
    } catch (err: unknown) {
      const message = err instanceof ApiError ? err.message : "Session sync failed";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  /**
   * Refetches user profile from GET /me using active JWT
   */
  const refreshProfile = useCallback(async () => {
    const token = getStoredToken();
    if (!token) {
      setUser(null);
      return;
    }

    try {
      const [rawUser, authenticatedEmail] = await Promise.all([
        batonApi.getMe(),
        getAuthenticatedEmail(),
      ]);
      setUser(formatUser(rawUser, authenticatedEmail));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setStoredToken(null);
        setUser(null);
      } else {
        throw err;
      }
    }
  }, []);

  /**
   * Initializes session on component mount
   */
  useEffect(() => {
    let isMounted = true;
    const initializeAuth = async () => {
      setLoading(true);
      setError(null);
      try {
        const token = getStoredToken();
        if (token) {
          try {
            const [raw, authenticatedEmail] = await Promise.all([
              batonApi.getMe(),
              getAuthenticatedEmail(),
            ]);
            if (isMounted) setUser(formatUser(raw, authenticatedEmail));
            return;
          } catch (e) {
            setStoredToken(null);
            if (isMounted) setUser(null);
            if (!(e instanceof ApiError && e.status === 401)) {
              throw e;
            }
          }
        } else {
          if (isMounted) setUser(null);
        }
      } catch (err) {
        if (isMounted) {
          setError(
            err instanceof Error ? err.message : "Failed to initialize authentication"
          );
          setUser(null);
        }
      } finally {
        if (isMounted) setLoading(false);
      }
    };

    initializeAuth();
    return () => {
      isMounted = false;
    };
  }, []);

  /**
   * Switch between roles seamlessly without reloading (Demo Mode only)
   */
  const switchRole = useCallback(
    async (role: Role) => {
      setError(null);
      if (user && !user.is_demo) {
        const msg = "Role switching is disabled for registered accounts. Demo Mode is required to test alternate roles.";
        setError(msg);
        throw new Error(msg);
      }
      try {
        await loginDemo(role);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Role switch failed");
        throw err;
      }
    },
    [loginDemo, user]
  );

  /**
   * Logout and clear session
   */
  const logout = useCallback(async () => {
    if (supabase) {
      await supabase.auth.signOut({ scope: "local" });
    }
    setStoredToken(null);
    setUser(null);
  }, []);

  /**
   * Reset backend demo database to seed state
   */
  const resetDemo = useCallback(async () => {
    setLoading(true);
    try {
      await batonApi.demoReset();
      await loginDemo("approver");
    } finally {
      setLoading(false);
    }
  }, [loginDemo]);

  const value = useMemo(
    () => ({
      user,
      loading,
      error,
      isAuthenticated: !!user,
      loginDemo,
      register,
      syncSession,
      switchRole,
      logout,
      resetDemo,
      refreshProfile,
    }),
    [user, loading, error, loginDemo, register, syncSession, switchRole, logout, resetDemo, refreshProfile]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
};

export default AuthContext;
