import React, { useState } from "react";
import { View, Text, TextInput, StyleSheet, Alert, TouchableOpacity, ActivityIndicator } from "react-native";
import { ApiError, apiFetch, saveSession } from "@/lib/api";
import { useRouter } from "expo-router";
import { API_CONFIG } from "@/lib/config";
import { AppContext } from "@/context/AppContext";
import { useContext } from 'react';

interface LoginPageProps {
  onSignupPress: () => void;
}

export default function LoginPage({ onSignupPress }: LoginPageProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const router = useRouter();
  const context = useContext(AppContext);

  if (!context) {
    throw new Error('LoginPage must be used within an AppProvider');
  }

  const { setIsLoggedIn } = context;

  const handleLogin = async () => {
    if (!username || !password) {
      Alert.alert('Error', 'Please enter both username and password');
      return;
    }

    setLoading(true);
    try {
      const data = await apiFetch(API_CONFIG.ENDPOINTS.LOGIN, {
        json: { employee_id: username.trim(), password },
        token: "",
      });
      if (!data.office) {
        Alert.alert("No office", "Your account is not assigned to an office yet. Ask your admin.");
        return;
      }
      await saveSession(data);
      setIsLoggedIn(true);
      router.replace("/(tabs)/user_dashboard");
    } catch (error: any) {
      if (error instanceof ApiError) {
        Alert.alert("Login Failed", error.message);
      } else {
        console.error("Login error:", error);
        Alert.alert("Error", "Failed to connect to the server. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={styles.container}>
      <View style={styles.content}>
        <Text style={styles.title}>Welcome Back</Text>
        <Text style={styles.subtitle}>Sign in to continue</Text>
        
        <View style={styles.inputContainer}>
          <TextInput
            style={styles.input}
            placeholder="Employee ID or email"
            value={username}
            onChangeText={setUsername}
            autoCapitalize="none"
            editable={!loading}
          />
          <TextInput
            style={styles.input}
            placeholder="Password"
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            editable={!loading}
          />
        </View>

        <TouchableOpacity
          style={[styles.button, loading && styles.buttonDisabled]}
          onPress={handleLogin}
          disabled={loading}
        >
          {loading ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.buttonText}>Sign In</Text>
          )}
        </TouchableOpacity>

        <TouchableOpacity
          onPress={onSignupPress}
          disabled={loading}
        >
          <Text style={styles.signupText}>Don't have an account? Sign up</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#F4E9FD',
  },
  content: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: 20,
  },
  title: {
    fontSize: 32,
    fontWeight: 'bold',
    marginBottom: 10,
    color: '#5B3B8E',
  },
  subtitle: {
    fontSize: 16,
    color: '#6A4CAF',
    marginBottom: 40,
  },
  inputContainer: {
    width: '100%',
    marginBottom: 20,
  },
  input: {
    width: '100%',
    height: 50,
    borderWidth: 1,
    borderColor: '#D9C2F0',
    borderRadius: 8,
    paddingHorizontal: 15,
    marginBottom: 15,
    fontSize: 16,
    backgroundColor: '#fff',
    color: '#5B3B8E',
  },
  button: {
    width: '100%',
    height: 50,
    backgroundColor: '#6A4CAF',
    borderRadius: 8,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 15,
  },
  buttonDisabled: {
    opacity: 0.7,
  },
  buttonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: 'bold',
  },
  signupText: {
    color: '#6A4CAF',
    fontSize: 16,
    textDecorationLine: 'underline',
  },
});
