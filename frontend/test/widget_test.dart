import 'package:flutter_test/flutter_test.dart';
import 'package:frontend/src/app.dart';

void main() {
  testWidgets('dashboard renders loading state', (WidgetTester tester) async {
    await tester.pumpWidget(const ClinicCopilotApp());

    expect(find.text('Loading clinician dashboard...'), findsOneWidget);
  });
}
