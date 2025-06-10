import React, { useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Image, Alert } from 'react-native';
import { API_CONFIG } from '@/app/config';
import * as SecureStore from 'expo-secure-store';

interface SummaryScreenProps {
  formData: any;
  onSubmit: () => void;
  isSignup?: boolean;
  loading?: boolean;
}

const SummaryScreen = ({ formData, onSubmit, isSignup = false, loading = false }: SummaryScreenProps) => {
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleCreateAccount = async () => {
    try {
      setIsSubmitting(true);

      // First API call: User registration
      console.log('Making user registration request to:', `${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.REGISTER}`);
      console.log('Request payload:', {
        name: formData.name,
        email: formData.email,
        phone: formData.phone,
        office_id: formData.office_id,
        device_id: formData.device_id,
        password: formData.password,
      });

      const userResponse = await fetch(
        `${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.REGISTER}`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            name: formData.name,
            email: formData.email,
            phone: formData.phone,
            office_id: formData.office_id,
            bluetooth_device: formData.device_id,
            password: formData.password,
          }),
        }
      );

      // Log the raw response
      const responseText = await userResponse.text();
      console.log('Raw user registration response:', responseText);

      // Try to parse the response as JSON
      let userResult;
      try {
        userResult = JSON.parse(responseText);
      } catch (parseError) {
        console.error('Failed to parse user registration response:', parseError);
        throw new Error(`Invalid response from server: ${responseText.substring(0, 100)}...`);
      }
      
      if (!userResult.employee_id) {
        throw new Error(userResult.message || 'User registration failed');
      }

      // Second API call: Face registration
      console.log('Making face registration request to:', `${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.REGISTER_FACE}`);
      
      const faceFormData = new FormData();
      faceFormData.append('image', formData.photo);
      faceFormData.append('employee_id', userResult.employee_id);

      const faceResponse = await fetch(
        `${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.REGISTER_FACE}`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'multipart/form-data',
          },
          body: faceFormData,
        }
      );

      // Log the raw response
      const faceResponseText = await faceResponse.text();
      console.log('Raw face registration response:', faceResponseText);

      // Try to parse the response as JSON
      let faceResult;
      try {
        faceResult = JSON.parse(faceResponseText);
      } catch (parseError) {
        console.error('Failed to parse face registration response:', parseError);
        throw new Error(`Invalid response from server: ${faceResponseText.substring(0, 100)}...`);
      }
      
      if (!faceResult.face_id) {
        throw new Error(faceResult.message || 'Face registration failed');
      }

      // Store employee_id in secure storage
      await SecureStore.setItemAsync('employee_id', userResult.employee_id);

      Alert.alert('Success', 'Account created successfully!');
      onSubmit();
    } catch (error) {
      console.error('Error during account creation:', error);
      Alert.alert('Error', error.message || 'Failed to create account. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  };

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

      {/* Bluetooth Device Section */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Bluetooth Device</Text>
        <Text style={styles.label}>Device Name:</Text>
        <Text style={styles.value}>{formData.device_name || 'Not selected'}</Text>
        <Text style={styles.label}>Device ID:</Text>
        <Text style={styles.value}>{formData.device_id || 'Not selected'}</Text>
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