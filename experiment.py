"""표준 라이브러리만으로 실행하는 HTTP 캐시 재검증 실험."""

import hashlib
import http.client
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class CacheHandler(BaseHTTPRequestHandler):
    # 서버가 본문을 바꾸면 같은 URL의 검증자도 함께 바뀐다.
    body = "캐시 실험 v1".encode("utf-8")
    policy = "public, max-age=30"

    def do_GET(self):
        if self.path != "/message":
            self.send_error(404)
            return

        body = self.body
        etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        # 이 실험은 단일 ETag만 다룬다. 와일드카드/목록 처리는 범위 밖이다.
        unchanged = self.headers.get("If-None-Match") == etag
        self.send_response(304 if unchanged else 200)
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", self.policy)
        if not unchanged:
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        # 304에는 본문을 보내지 않는다. 클라이언트가 가진 본문을 재사용한다.
        if not unchanged:
            self.wfile.write(body)

    def log_message(self, format, *args):
        # 아래 클라이언트가 실제 요청과 응답을 한곳에서 출력한다.
        pass


def main():
    # 포트 0은 운영체제가 빈 포트를 고르게 하므로 별도 포트 설정이 없다.
    server = HTTPServer(("127.0.0.1", 0), CacheHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    print(f"실험 URL: http://{host}:{port}/message")

    def request(etag=None):
        headers = {} if etag is None else {"If-None-Match": etag}
        connection = http.client.HTTPConnection(host, port, timeout=5)
        try:
            print(f"\n요청: GET /message, headers={headers!r}")
            connection.request("GET", "/message", headers=headers)
            response = connection.getresponse()
            status = response.status
            response_headers = dict(response.getheaders())
            body = response.read()
            print(f"응답: {status}, headers={response_headers!r}, body={body!r}")
            return status, response_headers, body
        finally:
            connection.close()

    try:
        status, headers, original_body = request()
        assert status == 200
        assert original_body == CacheHandler.body
        assert headers["Cache-Control"] == "public, max-age=30"
        original_etag = headers["ETag"]
        assert original_etag.startswith('"') and original_etag.endswith('"')

        status, headers, body = request(original_etag)
        assert status == 304
        assert body == b""
        assert headers["ETag"] == original_etag
        assert headers["Cache-Control"] == "public, max-age=30"

        # 실제 제공하는 본문 바이트를 변경한 뒤 이전 ETag로 재검증한다.
        CacheHandler.body = "변경된 캐시 실험 본문 v2".encode("utf-8")
        status, headers, body = request(original_etag)
        assert status == 200
        assert body == CacheHandler.body and body != original_body
        changed_etag = headers["ETag"]
        assert changed_etag != original_etag
        assert headers["Cache-Control"] == "public, max-age=30"

        # 조건부 헤더가 없으면 같은 본문이어도 서버는 200으로 응답한다.
        # 내용이 그대로일 때 ETag가 안정적으로 유지되는 것도 확인한다.
        status, headers, body = request()
        assert status == 200 and body == CacheHandler.body
        assert headers["ETag"] == changed_etag

        for policy in ("no-cache", "no-store"):
            CacheHandler.policy = policy
            status, headers, body = request()
            assert status == 200 and body == CacheHandler.body
            assert headers["Cache-Control"] == policy
            assert headers["ETag"] == changed_etag

            status, headers, body = request(changed_etag)
            assert status == 304 and body == b""
            assert headers["Cache-Control"] == policy
            assert headers["ETag"] == changed_etag

        # http.client와 일반 curl은 브라우저처럼 응답 캐시를 자동 관리하지
        # 않는다. 위 요청들은 모두 네트워크로 전송했고 조건부 헤더도 직접
        # 넣었다. 따라서 304는 서버 재검증의 증거이지 자동 캐시의 증거가 아니다.
        # 브라우저에서는 max-age=30인 저장 응답이 신선하면 일반적으로 요청
        # 없이 재사용한다. 신선도는 응답의 현재 나이도 고려하며, 만료는 삭제가
        # 아니다. 만료 후에는 저장한 ETag로 재검증할 수 있다.
        # no-cache는 저장을 허용하되 재사용 전 재검증을 요구한다.
        # no-store는 저장 금지이며 기존 캐시를 삭제하라는 명령이 아니다.
        # no-store에서도 수동 조건부 요청은 가능하므로 위 304만으로 클라이언트의
        # 저장 정책 준수를 입증할 수 없다. 브라우저 동작은 여기서 관찰하지 않았다.
        # 브라우저 실험 시 캐시 비활성화 설정과 새로고침 방식도 확인해야 한다.
        print("\n모든 실제 HTTP 요청/응답 검증 통과.")
        print("수동 조건부 요청 실험이며 브라우저 자동 캐시 관찰은 아닙니다.")
        print("max-age: 신선할 때 재사용 가능; no-cache: 재사용 전 재검증; no-store: 저장 금지.")
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


if __name__ == "__main__":
    main()
