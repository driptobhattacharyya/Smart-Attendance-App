import React, { useContext, useEffect } from 'react';
import { View, StyleSheet } from 'react-native';
import LoginPage from '../components/login_page';
import { useRouter } from 'expo-router';
import { AppContext } from './AppContext';

export default function IndexPage() {
  const router = useRouter();
  const context = useContext(AppContext);

  // If user is logged in, redirect to dashboard
  useEffect(() => {
    const checkAuth = async () => {
      if (context?.isLoggedIn) {
        router.replace('/(tabs)/user_dashboard');
      }
    };
    checkAuth();
  }, [context?.isLoggedIn]);

  // Always show login page if not logged in
  return (
    <View style={styles.container}>
      <LoginPage 
        onSignupPress={() => router.push('/signup')}
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