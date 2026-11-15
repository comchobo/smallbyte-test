"""표준 라이브러리만으로 실행하는 로컬 HTTP 메시지 검증 실험.

실행: python tests/fixtures/http/experiment.py
공개 파일이나 외부 서비스 없이 고정 리소스를 제공한다.
"""

import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

RESOURCE = "/message"
CONTENT = "안녕하세요, HTTP!\n".encode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        # 접근 로그 대신 아래에서 요청과 응답 메시지를 직접 출력한다.
        pass

    def respond_to_read(self, include_body):
        exists = self.path == RESOURCE
        body = CONTENT if exists else b"Resource not found\n"
        self.send_response(200 if exists else 404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        # 길이의 단위는 글자 수가 아니라 인코딩된 바이트 수다.
        # HEAD에서도 대응하는 GET 본문의 길이를 알리지만 본문은 보내지 않는다.
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()  # 헤더 뒤의 빈 줄이 헤더와 본문을 구분한다.
        if include_body:
            self.wfile.write(body)

    def do_GET(self):
        self.respond_to_read(include_body=True)

    def do_HEAD(self):
        self.respond_to_read(include_body=False)

    # POST 핸들러를 구현하지 않았다. BaseHTTPRequestHandler는 501을 반환한다.
    # 이는 POST 자체가 잘못되었다는 뜻이 아니라 이 서버의 기능 미지원이다.
    # 특정 리소스에서만 메서드를 허용하지 않는 405와도 의미가 다르다.


def exchange(server, method, path, body=None):
    host, port = server.server_address
    connection = http.client.HTTPConnection(host, port, timeout=5)
    headers = {"Host": f"{host}:{port}"}
    if body is not None:
        headers["Content-Type"] = "text/plain"
        headers["Content-Length"] = str(len(body))

    print(f"\n=== {method} {path} ===")
    print("송신 요청 (아래 줄바꿈은 실제 CRLF를 읽기 좋게 표시):")
    print(f"{method} {path} HTTP/1.1")
    for name, value in headers.items():
        print(f"{name}: {value}")
    print()
    print(f"요청 본문: {body!r}" if body is not None else "요청 본문: 없음")

    try:
        # 자동 헤더를 끄고 위에 출력한 헤더와 동일한 요청을 구성한다.
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.endheaders(body)
        response = connection.getresponse()
        response_headers = response.getheaders()
        response_body = response.read()
        status = response.status

        print("수신 응답:")
        version = {10: "HTTP/1.0", 11: "HTTP/1.1"}.get(
            response.version, str(response.version)
        )
        print(f"{version} {status} {response.reason}")
        for name, value in response_headers:
            print(f"{name}: {value}")
        print()
        print(f"응답 본문 ({len(response_body)} bytes): {response_body!r}")

        # HTTP 헤더 이름은 대소문자를 구분하지 않는다.
        received = {name.lower(): value for name, value in response_headers}
        assert "content-type" in received, f"{method}: 응답 본문 형식 헤더 누락"
        assert "content-length" in received, f"{method}: 응답 길이 헤더 누락"
        if method != "HEAD":
            assert int(received["content-length"]) == len(response_body), (
                f"{method}: 선언한 응답 길이와 실제 바이트 수가 다름"
            )
        return status, received, response_body
    finally:
        connection.close()


def main():
    # 0번 포트는 OS가 빈 포트를 선택하게 하므로 고정 포트 충돌을 피한다.
    # 루프백 주소만 사용하며 외부 네트워크에는 공개하지 않는다.
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"서버: http://127.0.0.1:{server.server_port}")
    print(f"고정 리소스: {RESOURCE}, UTF-8 본문: {CONTENT!r}")

    try:
        get_status, get_headers, get_body = exchange(server, "GET", RESOURCE)
        assert get_status == 200, "존재하는 리소스의 GET은 200이어야 함"
        assert get_body == CONTENT, "GET 응답이 고정 리소스 내용과 다름"

        head_status, head_headers, head_body = exchange(server, "HEAD", RESOURCE)
        assert head_status == get_status, "HEAD와 GET의 상태가 다름"
        assert head_headers["content-type"] == get_headers["content-type"], (
            "HEAD와 GET의 리소스 형식이 다름"
        )
        assert head_headers["content-length"] == get_headers["content-length"], (
            "HEAD는 대응하는 GET의 본문 길이를 알려야 함"
        )
        assert int(head_headers["content-length"]) > 0 and head_body == b"", (
            "HEAD는 길이 헤더가 양수여도 응답 본문을 전송하지 않음"
        )

        missing_status, _, missing_body = exchange(server, "GET", "/missing")
        assert missing_status == 404, "존재하지 않는 경로는 404이어야 함"
        assert missing_body, "이 서버의 404 응답에는 설명용 본문이 있음"
        # 404는 대상 리소스 부재다. 500은 서버 내부 처리 실패이며 서로 다르다.

        post_status, post_headers, post_body = exchange(
            server, "POST", RESOURCE, body=b"client payload\n"
        )
        assert post_status == 501, "POST 미구현 서버는 501을 반환해야 함"
        assert post_body, "미지원 오류 응답에도 설명용 본문이 있어야 함"
        assert post_headers["content-type"].startswith("text/html"), (
            "기본 오류 응답의 형식은 요청의 text/plain과 다를 수 있음"
        )
        # Host는 요청 대상을 나타낸다. 요청 Content-Type은 송신 본문의 형식,
        # 응답 Content-Type은 수신 본문의 형식이며 두 값은 독립적이다.
        # 날짜와 서버 버전은 달라질 수 있으므로 비교의 필수 조건으로 삼지 않는다.
        print("\n검증 통과: GET 200, HEAD 200 (본문 없음), GET /missing 404, POST 501")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive(), "서버 스레드가 종료되지 않음"
        print("로컬 서버 종료 완료")


if __name__ == "__main__":
    main()
