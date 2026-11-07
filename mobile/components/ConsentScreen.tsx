import React, { useCallback, useContext, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  NativeScrollEvent,
  NativeSyntheticEvent,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import MarkdownText from "@/components/MarkdownText";
import { AppContext } from "@/context/AppContext";
import { ApiError, apiFetch } from "@/lib/api";
import { API_CONFIG } from "@/lib/config";

type ConsentText = { version: number; title: string; body_markdown: string };

/**
 * Shown above the agree button. It is also the Google Play "prominent disclosure" for
 * background location, so it must stay in the app and must not depend on the server text.
 */
export const BACKGROUND_LOCATION_DISCLOSURE =
  "Attendify collects your location in the background, about every few minutes, while you are checked in. " +
  "This continues when the app is closed or the screen is off, and it stops when you check out. " +
  "Your location is used only for attendance.";

type Props = {
  /** Read-only view of the notice (from the dashboard). No agree button. */
  readOnly?: boolean;
  onClose?: () => void;
  /**
   * Signup mode: the registration's access token, used for POST /auth/consent. With
   * onAgreed/onDeclined set, the screen reports the choice instead of changing app state.
   */
  token?: string;
  onAgreed?: () => void;
  onDeclined?: () => void;
};

/**
 * Full-screen privacy notice. The root layout shows it instead of the app while the
 * logged-in user has not accepted the current consent version.
 */
export default function ConsentScreen({ readOnly = false, onClose, token, onAgreed, onDeclined }: Props) {
  const { setConsentRequired, handleLogout } = useContext(AppContext);
  const [text, setText] = useState<ConsentText | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [atBottom, setAtBottom] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [declined, setDeclined] = useState(false);
  const [viewportHeight, setViewportHeight] = useState(0);
  const [contentHeight, setContentHeight] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    setAtBottom(false);
    try {
      // Public endpoint: send no token.
      setText(await apiFetch<ConsentText>(API_CONFIG.ENDPOINTS.CONSENT_TEXT, { token: "" }));
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // A notice shorter than the screen has nothing to scroll, so it counts as read.
  useEffect(() => {
    if (viewportHeight > 0 && contentHeight > 0 && contentHeight <= viewportHeight + 8) setAtBottom(true);
  }, [viewportHeight, contentHeight]);

  const onScroll = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { layoutMeasurement, contentOffset, contentSize } = e.nativeEvent;
    if (layoutMeasurement.height + contentOffset.y >= contentSize.height - 24) setAtBottom(true);
  };

  const agree = async () => {
    if (!text) return;
    setSubmitting(true);
    try {
      await apiFetch(API_CONFIG.ENDPOINTS.CONSENT, { json: { version: text.version }, token });
      if (onAgreed) onAgreed();
      // Otherwise the root layout now shows the next screen; this one unmounts.
      else setConsentRequired(false);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        Alert.alert("Notice updated", "The privacy notice has changed. Please read the new version.");
        load();
      } else {
        Alert.alert("Error", "Could not save your consent. Check your connection and try again.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const renderBody = () => {
    if (loading) {
      return (
        <View style={styles.center}>
          <ActivityIndicator size="large" color="#6A4CAF" />
        </View>
      );
    }
    if (loadError || !text) {
      return (
        <View style={styles.center}>
          <Text style={styles.errorText}>The privacy notice could not be loaded.</Text>
          <TouchableOpacity style={styles.button} onPress={load}>
            <Text style={styles.buttonText}>Try again</Text>
          </TouchableOpacity>
        </View>
      );
    }
    return (
      <>
        <Text style={styles.title}>{text.title}</Text>
        <ScrollView
          style={styles.scroll}
          contentContainerStyle={styles.scrollContent}
          scrollEventThrottle={100}
          onScroll={onScroll}
          onLayout={(e) => setViewportHeight(e.nativeEvent.layout.height)}
          onContentSizeChange={(_, h) => setContentHeight(h)}
        >
          <MarkdownText source={text.body_markdown} />
        </ScrollView>
      </>
    );
  };

  const renderFooter = () => {
    if (readOnly || loading || loadError || !text) return null;
    if (declined) {
      return (
        <View style={styles.footer}>
          <Text style={styles.disclosure}>
            Attendance cannot be tracked without your consent, so you cannot use Attendify until you agree. You can
            read the notice again, or log out.
          </Text>
          <TouchableOpacity style={styles.button} onPress={() => setDeclined(false)}>
            <Text style={styles.buttonText}>Read the notice again</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.linkButton} onPress={() => handleLogout()}>
            <Text style={styles.link}>Log out</Text>
          </TouchableOpacity>
        </View>
      );
    }
    return (
      <View style={styles.footer}>
        <Text style={styles.disclosure}>{BACKGROUND_LOCATION_DISCLOSURE}</Text>
        {!atBottom && <Text style={styles.hint}>Scroll to the end of the notice to enable the button.</Text>}
        <TouchableOpacity
          style={[styles.button, (!atBottom || submitting) && styles.buttonDisabled]}
          onPress={agree}
          disabled={!atBottom || submitting}
        >
          {submitting ? <ActivityIndicator color="#fff" /> : <Text style={styles.buttonText}>I agree</Text>}
        </TouchableOpacity>
        <TouchableOpacity
          style={styles.linkButton}
          onPress={() => (onDeclined ? onDeclined() : setDeclined(true))}
          disabled={submitting}
        >
          <Text style={styles.link}>Not now</Text>
        </TouchableOpacity>
      </View>
    );
  };

  return (
    <SafeAreaView style={styles.container} edges={["top", "bottom"]}>
      {readOnly && (
        <View style={styles.header}>
          <Text style={styles.headerTitle}>Privacy notice</Text>
          <TouchableOpacity onPress={onClose} style={styles.closeButton}>
            <Text style={styles.link}>Close</Text>
          </TouchableOpacity>
        </View>
      )}
      <View style={styles.body}>{renderBody()}</View>
      {renderFooter()}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#F4E9FD" },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  headerTitle: { fontSize: 18, fontWeight: "bold", color: "#5B3B8E" },
  closeButton: { padding: 4 },
  body: { flex: 1, paddingHorizontal: 20, paddingTop: 12 },
  title: { fontSize: 26, fontWeight: "bold", color: "#5B3B8E", marginBottom: 12 },
  scroll: { flex: 1, backgroundColor: "#fff", borderRadius: 8, borderWidth: 1, borderColor: "#D9C2F0" },
  scrollContent: { padding: 16 },
  center: { flex: 1, justifyContent: "center", alignItems: "center", gap: 16 },
  errorText: { fontSize: 16, color: "#5B3B8E", textAlign: "center" },
  footer: { paddingHorizontal: 20, paddingTop: 12, paddingBottom: 12 },
  disclosure: { fontSize: 15, lineHeight: 22, color: "#4A3B94", marginBottom: 12 },
  hint: { fontSize: 14, color: "#6A4CAF", marginBottom: 8 },
  button: {
    width: "100%",
    height: 50,
    backgroundColor: "#6A4CAF",
    borderRadius: 8,
    justifyContent: "center",
    alignItems: "center",
    marginBottom: 8,
  },
  buttonDisabled: { opacity: 0.5 },
  buttonText: { color: "#fff", fontSize: 16, fontWeight: "bold" },
  linkButton: { alignItems: "center", padding: 12 },
  link: { color: "#6A4CAF", fontSize: 16, textDecorationLine: "underline" },
});
