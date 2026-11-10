import React, { useContext, useEffect, useState } from "react";
import { ActivityIndicator, Text, TextInput, TouchableOpacity, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { AppContext } from "@/context/AppContext";
import { acceptInvite, passwordProblem } from "@/lib/authApi";
import { errorMessage } from "@/lib/errors";
import { authStyles as styles } from "@/components/authStyles";

/**
 * Invite acceptance. Opened by the link in the invite e-mail (attendify://invite?token=...)
 * or by typing the code. Sets the password and signs in. There is no self-registration:
 * companies create their employees.
 */
export default function InviteScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ token?: string | string[] }>();
  const linkToken = Array.isArray(params.token) ? params.token[0] : params.token;
  const { startSession } = useContext(AppContext);
  const [token, setToken] = useState(linkToken ?? "");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // A link opened while this screen is already showing fills in the code.
  useEffect(() => {
    if (linkToken) setToken(linkToken);
  }, [linkToken]);

  const submit = async () => {
    if (!token.trim()) {
      setError("Enter the invite code from your email.");
      return;
    }
    const problem = passwordProblem(password, confirm);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setLoading(true);
    try {
      await acceptInvite(token, password);
      await startSession();
      router.replace("/(tabs)/user_dashboard");
    } catch (e) {
      setError(errorMessage(e, "Could not accept the invite. Please try again."));
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={styles.container}>
      <View style={styles.content}>
        <Text style={styles.title}>Join your team</Text>
        <Text style={styles.subtitle}>Choose a password to activate your Attendify account</Text>
        <View style={styles.inputContainer}>
          <TextInput
            style={styles.input}
            placeholder="Invite code"
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
        {error ? <Text style={styles.error}>{error}</Text> : null}
        <TouchableOpacity style={[styles.button, loading && styles.buttonDisabled]} onPress={submit} disabled={loading}>
          {loading ? <ActivityIndicator color="#fff" /> : <Text style={styles.buttonText}>Activate account</Text>}
        </TouchableOpacity>
        <TouchableOpacity onPress={() => router.replace("/")} disabled={loading}>
          <Text style={styles.link}>Back to sign in</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}
