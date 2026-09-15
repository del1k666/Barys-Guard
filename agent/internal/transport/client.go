package transport

import (
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"time"
)

const (
	requestTimeout = 30 * time.Second
	// Тело ошибки читается ограниченно: сервер за обратным прокси может
	// вернуть страницу в мегабайт, и складывать её в журнал незачем.
	maxErrorBody = 4 << 10
)

// StatusError — ответ сервера, отличный от успешного.
type StatusError struct {
	Code       int
	RetryAfter time.Duration
	Body       string
}

func (e *StatusError) Error() string {
	return fmt.Sprintf("сервер ответил %d: %s", e.Code, e.Body)
}

// IsForbidden отличает отзыв сертификата от прочих бед: повторять
// запрос в этом случае бессмысленно.
func IsForbidden(err error) bool {
	var statusErr *StatusError
	return errors.As(err, &statusErr) && statusErr.Code == http.StatusForbidden
}

// Client выполняет запросы к шлюзу. Создаётся в двух видах — бутстрапном
// и взаимном; см. NewBootstrap и NewMutual.
type Client struct {
	base *url.URL
	http *http.Client
}

func newClient(serverURL string, tlsConfig *tls.Config) (*Client, error) {
	base, err := url.Parse(serverURL)
	if err != nil {
		return nil, fmt.Errorf("разбор адреса сервера: %w", err)
	}
	transportConfig := http.DefaultTransport.(*http.Transport).Clone()
	transportConfig.TLSClientConfig = tlsConfig
	return &Client{
		base: base,
		http: &http.Client{Transport: transportConfig, Timeout: requestTimeout},
	}, nil
}

// NewBootstrap — клиент без собственного сертификата, для GET /ca и POST /enroll.
//
// Отдельный конструктор, а не необязательное поле: тип с опциональным
// сертификатом означал бы, что забытая настройка молча даёт анонимный
// запрос, а сервер отвечает 403 без объяснения причины.
func NewBootstrap(serverURL string, pool *x509.CertPool) (*Client, error) {
	return newClient(serverURL, &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12})
}

// NewMutual — клиент, предъявляющий сертификат агента.
func NewMutual(serverURL string, pool *x509.CertPool, pair tls.Certificate) (*Client, error) {
	return newClient(serverURL, &tls.Config{
		RootCAs:      pool,
		Certificates: []tls.Certificate{pair},
		MinVersion:   tls.VersionTLS12,
	})
}

func parseRetryAfter(value string) time.Duration {
	seconds, err := strconv.Atoi(value)
	if err != nil || seconds < 0 {
		return 0
	}
	return time.Duration(seconds) * time.Second
}

// do выполняет запрос и превращает неуспешный статус в *StatusError.
func (c *Client) do(ctx context.Context, req *http.Request) (*http.Response, error) {
	resp, err := c.http.Do(req.WithContext(ctx))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode >= 200 && resp.StatusCode < 400 {
		return resp, nil
	}

	body, _ := io.ReadAll(io.LimitReader(resp.Body, maxErrorBody))
	resp.Body.Close()
	return nil, &StatusError{
		Code:       resp.StatusCode,
		RetryAfter: parseRetryAfter(resp.Header.Get("Retry-After")),
		Body:       string(body),
	}
}

// PinOf считает отпечаток SHA-256 от DER первого сертификата в PEM.
func PinOf(caPEM []byte) (string, error) {
	block, _ := pem.Decode(caPEM)
	if block == nil {
		return "", errors.New("ответ не является сертификатом PEM")
	}
	sum := sha256.Sum256(block.Bytes)
	return hex.EncodeToString(sum[:]), nil
}

// FetchCA забирает CA по GET /gateway/v1/ca и принимает его, только если
// отпечаток совпал с ожидаемым.
//
// Соединение здесь заведомо непроверяемо — доверять ещё нечему. Подлинность
// даёт не транспорт, а отпечаток: это ровно та же схема, по которой
// закрепляют ключ хоста в ssh. Пустой отпечаток поэтому недопустим.
func FetchCA(ctx context.Context, serverURL, pin string) ([]byte, error) {
	if pin == "" {
		return nil, errors.New("загрузка CA по сети требует отпечатка --ca-pin")
	}

	base, err := url.Parse(serverURL)
	if err != nil {
		return nil, err
	}
	client := &http.Client{
		Timeout: requestTimeout,
		Transport: &http.Transport{
			// Проверка цепочки невозможна: проверяем отпечаток ниже.
			TLSClientConfig: &tls.Config{InsecureSkipVerify: true}, //nolint:gosec
		},
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, base.JoinPath("gateway", "v1", "ca").String(), nil)
	if err != nil {
		return nil, err
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, &StatusError{Code: resp.StatusCode}
	}
	caPEM, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return nil, err
	}

	actual, err := PinOf(caPEM)
	if err != nil {
		return nil, err
	}
	if actual != pin {
		return nil, fmt.Errorf("отпечаток CA %s не совпал с ожидаемым %s", actual, pin)
	}
	return caPEM, nil
}
