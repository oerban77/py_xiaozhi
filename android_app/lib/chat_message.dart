class ChatMessage {
  ChatMessage({
    required this.text,
    required this.isUser,
    DateTime? createdAt,
  }) : createdAt = createdAt ?? DateTime.now();

  final String text;
  final bool isUser;
  final DateTime createdAt;

  ChatMessage copyWith({String? text}) => ChatMessage(
        text: text ?? this.text,
        isUser: isUser,
        createdAt: createdAt,
      );
}