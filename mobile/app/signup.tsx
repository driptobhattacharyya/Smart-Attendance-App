import React, { useContext } from 'react';
import { View, StyleSheet } from 'react-native';
import MultiStepForm from '../components/MultiStepForm';
import { useRouter } from 'expo-router';
import { AppContext } from '@/context/AppContext';

export default function SignupPage() {
  const router = useRouter();
  const { setIsLoggedIn } = useContext(AppContext);

  return (
    <View style={styles.container}>
      <MultiStepForm 
        isSignup={true}
        onExit={() => router.push('/')}
        onComplete={() => {
          // Registration already saved the session; go straight in.
          setIsLoggedIn(true);
          router.replace('/(tabs)/user_dashboard');
        }}
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