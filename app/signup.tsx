import React from 'react';
import { View, StyleSheet } from 'react-native';
import MultiStepForm from '../components/MultiStepForm';
import { useRouter } from 'expo-router';

export default function SignupPage() {
  const router = useRouter();

  return (
    <View style={styles.container}>
      <MultiStepForm 
        isSignup={true}
        onExit={() => router.push('/')}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#fff',
  },
}); 