package transport

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
)

// request выполняет запрос с телом JSON и разбирает ответ в out.
// Если out равен nil, тело ответа отбрасывается.
func (c *Client) request(ctx context.Context, method string, segments []string, body, out any) error {
	var reader *bytes.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(raw)
	} else {
		reader = bytes.NewReader(nil)
	}

	req, err := http.NewRequest(method, c.base.JoinPath(segments...).String(), reader)
	if err != nil {
		return err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := c.do(ctx, req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if out == nil {
		return nil
	}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return fmt.Errorf("разбор ответа %s: %w", req.URL.Path, err)
	}
	return nil
}

// Whoami подтверждает, что сервер узнаёт агента по его сертификату.
func (c *Client) Whoami(ctx context.Context) (map[string]any, error) {
	var out map[string]any
	err := c.request(ctx, http.MethodGet, []string{"gateway", "v1", "whoami"}, nil, &out)
	return out, err
}

func (c *Client) Enroll(ctx context.Context, in EnrollRequest) (EnrollResponse, error) {
	var out EnrollResponse
	err := c.request(ctx, http.MethodPost, []string{"gateway", "v1", "enroll"}, in, &out)
	return out, err
}

func (c *Client) Renew(ctx context.Context, csrPEM string) (RenewResponse, error) {
	var out RenewResponse
	err := c.request(ctx, http.MethodPost, []string{"gateway", "v1", "renew"}, RenewRequest{CSRPEM: csrPEM}, &out)
	return out, err
}

func (c *Client) Heartbeat(ctx context.Context, in HeartbeatRequest) (HeartbeatResponse, error) {
	var out HeartbeatResponse
	err := c.request(ctx, http.MethodPost, []string{"gateway", "v1", "heartbeat"}, in, &out)
	return out, err
}

// Config забирает документ конфигурации. Второе значение равно true,
// если сервер ответил 304 и документ у агента уже актуален.
//
// Реализовано отдельно от request: 304 не несёт тела, и общий разбор
// JSON на нём споткнулся бы.
func (c *Client) Config(ctx context.Context, etag string) (ConfigResponse, bool, error) {
	req, err := http.NewRequest(http.MethodGet, c.base.JoinPath("gateway", "v1", "config").String(), nil)
	if err != nil {
		return ConfigResponse{}, false, err
	}
	if etag != "" {
		req.Header.Set("If-None-Match", etag)
	}

	resp, err := c.do(ctx, req)
	if err != nil {
		return ConfigResponse{}, false, err
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotModified {
		return ConfigResponse{}, true, nil
	}

	var out ConfigResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return ConfigResponse{}, false, fmt.Errorf("разбор конфигурации: %w", err)
	}
	return out, false, nil
}

func (c *Client) CommandResult(ctx context.Context, commandID string, in CommandResultRequest) error {
	return c.request(
		ctx, http.MethodPost,
		[]string{"gateway", "v1", "commands", commandID, "result"},
		in, nil,
	)
}

// SendEvents отправляет пакет событий (NDJSON, по строке на событие).
//
// Отдельно от request: тело — не JSON, а готовые строки, и Content-Type другой.
func (c *Client) SendEvents(ctx context.Context, ndjson []byte) (EventsResult, error) {
	req, err := http.NewRequest(
		http.MethodPost,
		c.base.JoinPath("gateway", "v1", "events").String(),
		bytes.NewReader(ndjson),
	)
	if err != nil {
		return EventsResult{}, err
	}
	req.Header.Set("Content-Type", "application/x-ndjson")

	resp, err := c.do(ctx, req)
	if err != nil {
		return EventsResult{}, err
	}
	defer resp.Body.Close()

	var out EventsResult
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return EventsResult{}, fmt.Errorf("разбор ответа приёма событий: %w", err)
	}
	return out, nil
}
