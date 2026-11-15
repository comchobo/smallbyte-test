"""표준 라이브러리로 실행하는 DNS → TCP → TLS 단계별 실험."""

import multiprocessing
import queue
import socket
import ssl


HOST = "example.com"
PORT = 443
TIMEOUT = 5


def resolve_worker(host, results):
    try:
        addresses = socket.getaddrinfo(host, PORT, type=socket.SOCK_STREAM)
        results.put((True, addresses))
    except Exception as exc:
        results.put((False, (type(exc).__name__, str(exc))))


def resolve(host):
    # getaddrinfo 자체에는 타임아웃이 없어 별도 프로세스로 대기 시간을 제한한다.
    # 운영체제의 resolver는 캐시/hosts를 사용할 수 있어 외부 DNS 질의를 보장하지 않는다.
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    process = context.Process(target=resolve_worker, args=(host, results))
    process.start()
    try:
        try:
            success, result = results.get(timeout=TIMEOUT)
        except queue.Empty:
            print(f"DNS {host}: {TIMEOUT}초 내 결과 없음 (DNS 응답 코드 아님)")
            return None
        if not success:
            name, message = result
            print(f"DNS {host}: {name}: {message}")
            return None
        print(f"DNS {host}: 성공")
        for family, _, _, _, address in result:
            print(f"  {socket.AddressFamily(family).name} {address}")
        return result
    finally:
        process.join(timeout=1)
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join()
        results.close()
        results.join_thread()


def tls_experiment(selected, empty_trust=False):
    family, socktype, protocol, _, address = selected
    label = "빈 신뢰 저장소" if empty_trust else "기본 신뢰 저장소"
    # TCP 성공은 해당 IP·포트의 연결 성공일 뿐 TLS/HTTP 성공을 뜻하지 않는다.
    stage = "TCP 연결"
    try:
        with socket.socket(family, socktype, protocol) as tcp:
            tcp.settimeout(TIMEOUT)
            tcp.connect(address)
            print(f"{label}: TCP 성공, 상대={tcp.getpeername()}")
            stage = "TLS 설정"
            if empty_trust:
                # 기본 CA를 로드하지 않는다. 검증은 끄지 않으며 OS 설정도 바꾸지 않는다.
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            else:
                context = ssl.create_default_context()
            print(
                f"{label}: CA 수={context.cert_store_stats()['x509_ca']}, "
                f"verify_mode={context.verify_mode.name}, "
                f"check_hostname={context.check_hostname}"
            )
            # SNI는 서버의 인증서 선택에, 호스트 검증은 인증서 이름 확인에 쓰인다.
            # server_hostname은 원래 이름으로 유지하며 TCP 목적지는 선택 IP로 고정한다.
            stage = "TLS 핸드셰이크/인증"
            with context.wrap_socket(tcp, server_hostname=HOST) as tls:
                print(f"{label}: TLS 성공, 버전={tls.version()}")
                if empty_trust:
                    print("빈 신뢰 저장소에서도 성공함: 예상과 달라 원인 확인 필요")
                return True
    except Exception as exc:
        print(f"{label}: {stage} 실패: {type(exc).__name__}: {exc}")
        if isinstance(exc, ssl.SSLCertVerificationError):
            print(f"  검증 코드={exc.verify_code}, 설명={exc.verify_message}")
        return False


def local_tcp_failure():
    # 공개 포트 탐색 대신 로컬에서 포트를 확보하고 listen하지 않는다.
    # 준비 소켓을 유지해 포트 재할당 경쟁을 줄이며 공개 대상과 조건이 다름을 명시한다.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
            reserved.bind(("127.0.0.1", 0))
            address = reserved.getsockname()
            print(f"로컬 TCP: {address}, bind만 수행, listen 없음")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                client.settimeout(TIMEOUT)
                try:
                    client.connect(address)
                except OSError as exc:
                    print(f"로컬 TCP 연결 실패: {type(exc).__name__}: {exc}")
                else:
                    print("로컬 TCP 연결 성공: 예상과 달라 원인 확인 필요")
    except OSError as exc:
        print(f"로컬 TCP 준비 실패: {type(exc).__name__}: {exc}")


def main():
    print(f"대상={HOST}:{PORT}, 작업별 대기 제한={TIMEOUT}초")
    print("직접 연결 실험: 프록시/VPN/방화벽의 영향을 받을 수 있으며 우회하지 않음")
    addresses = resolve(HOST)
    selected = addresses[0] if addresses else None
    normal_ok = False
    if selected:
        print(f"선택={socket.AddressFamily(selected[0]).name} {selected[4]}")
        # 여러 주소 중 첫 주소만 사용한다. 실패가 전체 사이트 장애를 뜻하지 않는다.
        normal_ok = tls_experiment(selected)
    else:
        print("정상 TCP/TLS: 이름 해석 결과가 없어 실행하지 않음")

    invalid = resolve("smallbyte-test.invalid")
    if invalid:
        print(".invalid 주소가 반환됨: 로컬 resolver 설정 확인 필요, TCP 시도는 하지 않음")
    else:
        print(".invalid: 사용 가능한 주소 없음, TCP 시도하지 않음")

    local_tcp_failure()

    if selected:
        print("동일 선택 IP·포트·호스트 이름으로 새 연결, 클라이언트 신뢰 설정만 변경")
        tls_experiment(selected, empty_trust=True)
        if not normal_ok:
            print("정상 TLS가 성공하지 않아 신뢰 설정 변경만의 영향은 비교 확정할 수 없음")
    else:
        print("빈 신뢰 저장소 TLS: 선택 IP가 없어 실행하지 않음")
    # 빈 저장소의 인증 실패만으로 서버 인증서 자체가 잘못되었다고 판단하지 않는다.
    # 오류 문자열을 실제 출력하며 연결 거부/시간 초과/인증 실패를 동일시하지 않는다.
    print("TLS 성공도 HTTP 응답 성공은 아님: 이 실험은 HTTP 요청을 보내지 않음")


if __name__ == "__main__":
    main()
