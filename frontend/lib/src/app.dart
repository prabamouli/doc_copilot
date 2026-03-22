import 'package:flutter/material.dart';

import 'dashboard.dart';

class ClinicCopilotApp extends StatelessWidget {
  const ClinicCopilotApp({super.key});

  @override
  Widget build(BuildContext context) {
    const baseSand = Color(0xFFF6F0E8);
    const ink = Color(0xFF12212B);
    const clay = Color(0xFFC96F4A);
    const teal = Color(0xFF1D6A72);

    final theme = ThemeData(
      useMaterial3: true,
      scaffoldBackgroundColor: baseSand,
      colorScheme: ColorScheme.fromSeed(
        seedColor: clay,
        primary: clay,
        secondary: teal,
        surface: const Color(0xFFFFFBF6),
      ),
      textTheme: const TextTheme(
        displaySmall: TextStyle(fontSize: 34, fontWeight: FontWeight.w700, color: ink, height: 1.05),
        headlineSmall: TextStyle(fontSize: 24, fontWeight: FontWeight.w700, color: ink),
        titleLarge: TextStyle(fontSize: 18, fontWeight: FontWeight.w700, color: ink),
        titleMedium: TextStyle(fontSize: 15, fontWeight: FontWeight.w600, color: ink),
        bodyLarge: TextStyle(fontSize: 15, color: ink, height: 1.45),
        bodyMedium: TextStyle(fontSize: 13, color: ink, height: 1.45),
        labelLarge: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: ink),
      ),
      cardTheme: CardThemeData(
        color: Colors.white.withValues(alpha: 0.78),
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(28),
          side: BorderSide(color: Colors.black.withValues(alpha: 0.06)),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: Colors.white.withValues(alpha: 0.8),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(18),
          borderSide: BorderSide(color: Colors.black.withValues(alpha: 0.08)),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(18),
          borderSide: BorderSide(color: Colors.black.withValues(alpha: 0.08)),
        ),
        focusedBorder: const OutlineInputBorder(
          borderRadius: BorderRadius.all(Radius.circular(18)),
          borderSide: BorderSide(color: teal, width: 1.4),
        ),
      ),
    );

    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'Clinic Copilot',
      theme: theme,
      home: const DashboardScreen(),
    );
  }
}
