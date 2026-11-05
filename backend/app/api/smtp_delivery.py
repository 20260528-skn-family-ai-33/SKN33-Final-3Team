"""SMTP 전송 전용 자식 프로세스. stdin 입력만 사용하고 비밀값·예외를 출력하지 않는다."""
import json
import smtplib
import ssl
import sys
from email.message import EmailMessage


def deliver(data):
    message = EmailMessage()
    message['Subject'] = '[Borderless] 임시 비밀번호 안내'
    message['From'], message['To'] = data['sender'], data['recipient']
    message.set_content(f"임시 비밀번호: {data['temporary_password']}\n\n이 비밀번호로 로그인한 뒤 계정 설정에서 비밀번호를 변경해 주세요.\n재발급하면 이전 비밀번호는 사용할 수 없습니다.")
    context = ssl.create_default_context()
    client = (smtplib.SMTP_SSL(data['host'], data['port'], timeout=data['timeout'], context=context)
              if data['mode'] == 'ssl' else smtplib.SMTP(data['host'], data['port'], timeout=data['timeout']))
    try:
        if data['mode'] == 'starttls':
            client.starttls(context=context)
        if data.get('username'):
            client.login(data['username'], data['password'])
        refused = client.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
    finally:
        # DATA 성공 후 QUIT 응답을 기다리다가 상한에 걸려 성공을 뒤집지 않도록 즉시 소켓을 닫는다.
        try:
            client.close()
        except (OSError, smtplib.SMTPException):
            pass


if __name__ == '__main__':
    try:
        deliver(json.load(sys.stdin.buffer))
    except Exception:
        sys.exit(1)
