"""표준 라이브러리만 사용하는 로컬 쿠키·세션 실험.

HTTP 요청 자체는 이전 요청의 로그인 결과를 기억하지 않는다. 서버는
메모리에 상태를 저장할 수 있으며, 매 요청의 쿠키로 그 상태를 찾아간다.
실제 인증 시스템이 아니라 고정 사용자 demo를 위한 일회성 실험이다.

HttpOnly는 브라우저 JavaScript의 쿠키 읽기를 제한하며 요청 전송은 막지
않는다. Secure는 원칙적으로 HTTPS 전송을 위한 속성이므로 이 HTTP 실험은
생략한다. 로컬 호스트에 대한 브라우저 예외를 일반 HTTP에 적용하면 안 된다.
SameSite=Lax는 교차 사이트 전송을 제한하지만 이 실험은 같은 사이트만
사용한다. 포트 차이만으로 교차 사이트가 되지는 않는다. SameSite=None은
일반적으로 Secure도 요구한다. 아래 Python 클라이언트는 브라우저가 아니므로
JavaScript 접근 제한이나 브라우저의 Secure/SameSite 정책을 검증하지 않는다.
"""

from http.client import HTTPConnection
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
import secrets
from threading import Thread


def main():
    # 서버를 재시작하지 않고 모든 요청을 비교한다. 종료하면 세션도 사라진다.
    sessions = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # 기본 접근 로그도 끈다. 실제 식별자는 어떤 로그에도 출력하지 않는다.
            pass

        def session_id(self):
            cookies = SimpleCookie()
            cookies.load(self.headers.get('Cookie', ''))
            cookie = cookies.get('lab_session')
            return cookie.value if cookie is not None else None

        def reply(self, status, body, cookie=None):
            data = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            if cookie is not None:
                self.send_header('Set-Cookie', cookie)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path != '/me':
                return self.reply(404, 'not found')
            user = sessions.get(self.session_id())
            if user is None:
                return self.reply(401, 'login required')
            self.reply(200, user)

        def do_POST(self):
            if self.path == '/login':
                token = secrets.token_urlsafe(32)
                sessions[token] = 'demo'
                return self.reply(
                    200,
                    'logged in',
                    f'lab_session={token}; Path=/; HttpOnly; SameSite=Lax',
                )
            if self.path == '/logout':
                # 서버 무효화와 클라이언트 쿠키 만료는 별개의 작업이다.
                sessions.pop(self.session_id(), None)
                return self.reply(
                    200,
                    'logged out',
                    'lab_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax',
                )
            self.reply(404, 'not found')

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    issued_token = None

    def request(label, method, path, expected_status, expected_body, cookie=None):
        connection = HTTPConnection(*server.server_address, timeout=5)
        try:
            headers = {} if cookie is None else {'Cookie': cookie}
            connection.request(method, path, headers=headers)
            response = connection.getresponse()
            body = response.read().decode('utf-8')
            set_cookie = response.getheader('Set-Cookie')
            # 실패 메시지에도 원본 헤더나 토큰을 넣지 않는다.
            assert response.status == expected_status, f'{label}: 상태 코드 불일치'
            assert body == expected_body, f'{label}: 응답 본문 불일치'
            print(f'{label}: {method} {path} -> {response.status}, {body}')
            if cookie is not None:
                print('  Cookie: lab_session=DUMMY_SESSION_A')
            if set_cookie is not None:
                if path == '/login':
                    parsed = SimpleCookie()
                    parsed.load(set_cookie)
                    morsel = parsed.get('lab_session')
                    assert morsel is not None and morsel.value, '세션 쿠키 누락'
                    assert morsel['path'] == '/', '쿠키 경로 불일치'
                    assert morsel['httponly'], 'HttpOnly 누락'
                    assert morsel['samesite'] == 'Lax', 'SameSite 불일치'
                    assert not morsel['secure'], '로컬 HTTP 실험 조건 불일치'
                    print('  Set-Cookie: lab_session=DUMMY_SESSION_A; '
                          'Path=/; HttpOnly; SameSite=Lax')
                else:
                    assert set_cookie == (
                        'lab_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax'
                    ), '쿠키 삭제 지시 불일치'
                    print('  Set-Cookie: lab_session=; Path=/; Max-Age=0; '
                          'HttpOnly; SameSite=Lax')
            return set_cookie
        finally:
            connection.close()

    try:
        request('쿠키 없음 1', 'GET', '/me', 401, 'login required')
        request('쿠키 없음 2', 'GET', '/me', 401, 'login required')
        header = request('로그인', 'POST', '/login', 200, 'logged in')
        assert header is not None, '로그인 Set-Cookie 누락'
        cookies = SimpleCookie()
        cookies.load(header)
        issued_token = cookies['lab_session'].value
        saved_cookie = f'lab_session={issued_token}'
        assert sessions.get(issued_token) == 'demo', '서버 세션 생성 실패'

        # 이 클라이언트는 자동 쿠키 저장소가 없어 쿠키를 명시적으로 전달한다.
        # cookie=None은 클라이언트에서 쿠키를 지워 전송하지 않는 상황에 해당한다.
        request('쿠키 포함', 'GET', '/me', 200, 'demo', saved_cookie)
        request('쿠키 제거 후', 'GET', '/me', 401, 'login required')
        assert issued_token in sessions, '쿠키 제거만으로 서버 세션이 사라짐'
        request('삭제 전 식별자 재전송', 'GET', '/me', 200, 'demo', saved_cookie)

        request('로그아웃', 'POST', '/logout', 200, 'logged out', saved_cookie)
        assert issued_token not in sessions, '서버 세션 무효화 실패'
        request('로그아웃 후 이전 식별자 재전송', 'GET', '/me', 401,
                'login required', saved_cookie)
        print('통과: 쿠키 제거는 서버 세션을 없애지 않지만 로그아웃은 무효화한다.')
        print('브라우저 전용 쿠키 정책은 검증하지 않았으며 모든 표본 식별자는 더미다.')
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        sessions.clear()


if __name__ == '__main__':
    main()
