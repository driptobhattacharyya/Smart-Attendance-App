import React, { useContext, useState } from "react";
import { ActivityIndicator, Text, TextInput, TouchableOpacity, View } from "react-native";
import { useRouter } from "expo-router";
import { AppContext } from "@/context/AppContext";
import { loginWithEmail, loginWithEmployeeCode } from "@/lib/authApi";
import { errorMessage } from "@/lib/errors";
import { authStyles as styles } from "./authStyles";

type Mode = "email" | "code";

interface LoginPageProps {
  onInvitePress: () => void;
  onForgotPress: () => void;
}

export default function LoginPage({ onInvitePress, onForgotPress }: LoginPageProps) {
  const [mode, setMode] = useState<Mode>("email");
  const [email, setEmail] = useState("");
  const [orgSlug, setOrgSlug] = useState("");
  const [employeeCode, setEmployeeCode] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();
  const { startSession, authNotice, clearAuthNotice } = useContext(AppContext);

  const handleLogin = async () => {
    const missing = mode === "email" ? !email.trim() : !orgSlug.trim() || !employeeCode.trim();
    if (missing || !password) {
      setError(mode === "email" ? "Enter your email and password." : "Enter your company code, employee ID and password.");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      if (mode === "email") await loginWithEmail(email, password);
      else await loginWithEmployeeCode(orgSlug, employeeCode, password);
      await startSession();
      router.replace("/(tabs)/user_dashboard");
    } catch (e) {
      setError(errorMessage(e, "Could not sign in. Please try again."));
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={styles.container}>
      <View style={styles.content}>
        <Text style={styles.title}>Attendify</Text>
        <Text style={styles.subtitle}>Sign in with the account your company created for you</Text>

        {authNotice ? (
          <View style={styles.notice}>
            <Text style={styles.noticeText}>{authNotice}</Text>
          </View>
        ) : null}

        <View style={styles.modeRow}>
          {(["email", "code"] as Mode[]).map((m) => (
            <TouchableOpacity
              key={m}
              style={[styles.modeButton, mode === m && styles.modeButtonActive]}
              onPress={() => {
                setMode(m);
                setError(null);
              }}
              disabled={loading}
            >
              <Text style={[styles.modeText, mode === m && styles.modeTextActive]}>
                {m === "email" ? "Email" : "Company code"}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        <View style={styles.inputContainer}>
          {mode === "email" ? (
            <TextInput
              style={styles.input}
              placeholder="Email"
              value={email}
              onChangeText={setEmail}
              autoCapitalize="none"
              autoComplete="email"
              keyboardType="email-address"
              editable={!loading}
            />
          ) : (
            <>
              <TextInput
                style={styles.input}
                placeholder="Company code"
                value={orgSlug}
                onChangeText={setOrgSlug}
                autoCapitalize="none"
                autoCorrect={false}
                editable={!loading}
              />
              <TextInput
                style={styles.input}
                placeholder="Employee ID"
                value={employeeCode}
                onChangeText={setEmployeeCode}
                autoCapitalize="none"
                autoCorrect={false}
                editable={!loading}
              />
            </>
          )}
          <TextInput
            style={styles.input}
            placeholder="Password"
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            autoComplete="current-password"
            editable={!loading}
            onSubmitEditing={handleLogin}
          />
        </View>

        {error ? <Text style={styles.error}>{error}</Text> : null}

        <TouchableOpacity
          style={[styles.button, loading && styles.buttonDisabled]}
          onPress={() => {
            clearAuthNotice();
            handleLogin();
          }}
          disabled={loading}
        >
          {loading ? <ActivityIndicator color="#fff" /> : <Text style={styles.buttonText}>Sign In</Text>}
        </TouchableOpacity>

        <TouchableOpacity onPress={onForgotPress} disabled={loading}>
          <Text style={styles.link}>Forgot password?</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={onInvitePress} disabled={loading}>
          <Text style={styles.link}>I have an invite code</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}
