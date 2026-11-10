import React, { useState } from "react";
import { ActivityIndicator, Text, TextInput, TouchableOpacity, View } from "react-native";
import { useRouter } from "expo-router";
import { passwordProblem, requestPasswordReset, resetPassword } from "@/lib/authApi";
import { errorMessage } from "@/lib/errors";
import { authStyles as styles } from "@/components/authStyles";

type Step = "request" | "reset";

/** Forgot password: ask for the e-mail, then (with the code from it) choose a new password. */
export default function ForgotPasswordScreen() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("request");
  const [email, setEmail] = useState("");
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const request = async () => {
    if (!email.trim()) {
      setError("Enter your email address.");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const res = await requestPasswordReset(email);
      setInfo(res?.message ?? "If that email has an account, we sent a reset code to it.");
      setStep("reset");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  const reset = async () => {
    const problem = !token.trim() ? "Enter the reset code from the email." : passwordProblem(password, confirm);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setLoading(true);
    try {
      await resetPassword(token, password);
      setInfo("Your password was changed. Sign in with the new password.");
      router.replace("/");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={styles.container}>
      <View style={styles.content}>
        <Text style={styles.title}>Reset password</Text>
        {step === "request" ? (
          <>
            <Text style={styles.subtitle}>We will email you a code to choose a new password</Text>
            <View style={styles.inputContainer}>
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
            </View>
          </>
        ) : (
          <>
            {info ? <Text style={styles.success}>{info}</Text> : null}
            <View style={styles.inputContainer}>
              <TextInput
                style={styles.input}
                placeholder="Reset code"
                value={token}
                onChangeText={setToken}
                autoCapitalize="none"
                autoCorrect={false}
                editable={!loading}
              />
              <TextInput
                style={styles.input}
                placeholder="New password (at least 10 characters)"
                value={password}
                onChangeText={setPassword}
                secureTextEntry
                autoComplete="new-password"
                editable={!loading}
              />
              <TextInput
                style={styles.input}
                placeholder="Repeat password"
                value={confirm}
                onChangeText={setConfirm}
                secureTextEntry
                autoComplete="new-password"
                editable={!loading}
              />
            </View>
          </>
        )}
        {error ? <Text style={styles.error}>{error}</Text> : null}
        <TouchableOpacity
          style={[styles.button, loading && styles.buttonDisabled]}
          onPress={step === "request" ? request : reset}
          disabled={loading}
        >
          {loading ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.buttonText}>{step === "request" ? "Send code" : "Change password"}</Text>
          )}
        </TouchableOpacity>
        {step === "request" && (
          <TouchableOpacity
            onPress={() => {
              setError(null);
              setStep("reset");
            }}
            disabled={loading}
          >
            <Text style={styles.link}>I already have a code</Text>
          </TouchableOpacity>
        )}
        <TouchableOpacity onPress={() => router.replace("/")} disabled={loading}>
          <Text style={styles.link}>Back to sign in</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}
