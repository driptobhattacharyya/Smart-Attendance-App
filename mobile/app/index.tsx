import React, { useContext, useEffect } from "react";
import { View, StyleSheet } from "react-native";
import LoginPage from "../components/login_page";
import { useRouter } from "expo-router";
import { AppContext } from "@/context/AppContext";

export default function IndexPage() {
  const router = useRouter();
  const context = useContext(AppContext);

  // If user is logged in, redirect to dashboard
  useEffect(() => {
    if (context?.isLoggedIn) router.replace("/(tabs)/user_dashboard");
  }, [context?.isLoggedIn]);

  return (
    <View style={styles.container}>
      <LoginPage onInvitePress={() => router.push("/invite")} onForgotPress={() => router.push("/forgot-password")} />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#fff",
  },
});
