import React, { useContext, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Image, Alert } from 'react-native';
import { API_CONFIG } from "@/lib/config";
import { apiFetch, enrolFace, saveSession } from '@/lib/api';
import { AppContext } from '@/context/AppContext';
import ConsentScreen from '@/components/ConsentScreen';

interface SummaryScreenProps {
  formData: any;
  onSubmit?: (session: any) => void;
  isSignup?: boolean;
  loading?: boolean;
}

const SummaryScreen = ({ formData, onSubmit, isSignup = false, loading = false }: SummaryScreenProps) => {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const { setConsentRequired, setFaceEnrolled } = useContext(AppContext);

  // Kept between attempts so a failed face upload can be retried
  // without creating the account twice.
  const [auth, setAuth] = useState<any>(null);

  // Set when the new account still needs to accept the privacy notice. The notice replaces
  // this summary; face enrolment waits until it is accepted, because the API refuses it before.
  const [consentStep, setConsentStep] = useState(false);

  /** Face enrolment, then save the session and finish signup. Used once consent is in place. */
  const completeAccount = async (session: any) => {
    try {
      setIsSubmitting(true);
      await enrolFace(formData.photo, session.access_token);
      await saveSession({ ...session, consent_required: false, face_enrolled: true });
      setConsentRequired(false);
      setFaceEnrolled(true);
      Alert.alert(
        'Welcome!',
        `Your account is ready. Your employee ID is ${session.employee_id}.`
      );
      onSubmit?.(session);
    } catch (error: any) {
      console.warn('Error during account creation:', error);
      const prefix = 'Account created, but face registration failed: ';
      Alert.alert('Error', prefix + (error.message || 'Please try again.'));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCreateAccount = async () => {
    try {
      setIsSubmitting(true);

      let session = auth;
      if (!session) {
        session = await apiFetch(API_CONFIG.ENDPOINTS.REGISTER, {
          token: "",
          json: {
            name: formData.name,
            email: formData.email,
            phone: formData.phone,
            office_id: formData.office_id,
            password: formData.password,
          },
        });
        setAuth(session);
      }

      if (session.consent_required) {
        setConsentStep(true);
        return;
      }
      await completeAccount(session);
    } catch (error: any) {
      console.warn('Error during account creation:', error);
      const prefix = auth ? 'Account created, but face registration failed: ' : '';
      Alert.alert('Error', prefix + (error.message || 'Please try again.'));
    } finally {
      setIsSubmitting(false);
    }
  };

  /** The user agreed: consent is already saved on the server, so enrol the face and finish. */
  const handleAgreed = async () => {
    setConsentStep(false);
    const session = { ...auth, consent_required: false };
    setAuth(session);
    await completeAccount(session);
  };

  /** The user chose "Not now": keep the account, but they must accept before using attendance. */
  const handleDeclined = async () => {
    setConsentStep(false);
    await saveSession({ ...auth, face_enrolled: false });
    setConsentRequired(true);
    Alert.alert(
      'Account created',
      'You must accept the privacy notice before you can use attendance. You will see it when you open the app.'
    );
    onSubmit?.(auth);
  };

  if (consentStep && auth) {
    return (
      <ConsentScreen
        token={auth.access_token}
        onAgreed={handleAgreed}
        onDeclined={handleDeclined}
      />
    );
  }

  return (
    <ScrollView style={styles.container}>
      <Text style={styles.title}>Summary</Text>
      
      {/* Photo Preview */}
      {formData.photo && (
        <View style={styles.photoSection}>
          <Text style={styles.sectionTitle}>Photo</Text>
          <Image 
            source={{ uri: formData.photo.uri }} 
            style={styles.photoPreview}
          />
        </View>
      )}
      
      {/* Office Section */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Office</Text>
        <Text style={styles.label}>Office Name:</Text>
        <Text style={styles.value}>{formData.office_name || 'Not selected'}</Text>
        <Text style={styles.label}>Address:</Text>
        <Text style={styles.value}>{formData.office_address || 'Not selected'}</Text>
      </View>

      {/* Form Data Section */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Personal Information</Text>
        <Text style={styles.label}>Name:</Text>
        <Text style={styles.value}>{formData.name || 'Not provided'}</Text>
        
        <Text style={styles.label}>Email:</Text>
        <Text style={styles.value}>{formData.email || 'Not provided'}</Text>
        
        <Text style={styles.label}>Phone:</Text>
        <Text style={styles.value}>{formData.phone || 'Not provided'}</Text>
      </View>

      {/* Submit Button */}
      <TouchableOpacity
        style={[styles.submitButton, (loading || isSubmitting) && styles.submitButtonDisabled]} 
        onPress={isSignup ? handleCreateAccount : onSubmit}
        disabled={loading || isSubmitting}
      >
        {loading || isSubmitting ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Text style={styles.submitButtonText}>
            {isSignup ? 'Create Account' : 'Submit'}
          </Text>
        )}
      </TouchableOpacity>
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#F8F4FF',
    padding: 20,
  },
  title: {
    fontSize: 24,
    fontWeight: 'bold',
    color: '#4A3B94',
    marginBottom: 20,
  },
  photoSection: {
    backgroundColor: '#fff',
    borderRadius: 10,
    padding: 15,
    marginBottom: 20,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  photoPreview: {
    width: '100%',
    height: 200,
    borderRadius: 10,
    marginTop: 10,
  },
  section: {
    backgroundColor: '#fff',
    borderRadius: 10,
    padding: 15,
    marginBottom: 20,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: '600',
    color: '#6A5ACD',
    marginBottom: 10,
  },
  label: {
    fontSize: 16,
    color: '#4A3B94',
    fontWeight: '500',
    marginTop: 8,
  },
  value: {
    fontSize: 16,
    color: '#333',
    marginBottom: 4,
  },
  submitButton: {
    backgroundColor: '#6A5ACD',
    padding: 15,
    borderRadius: 10,
    alignItems: 'center',
    marginTop: 20,
  },
  submitButtonDisabled: {
    backgroundColor: '#B8B8B8',
  },
  submitButtonText: {
    color: '#fff',
    fontSize: 18,
    fontWeight: '600',
  },
});

export default SummaryScreen; 