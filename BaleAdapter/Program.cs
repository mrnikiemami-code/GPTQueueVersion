using System.Net.Http.Headers;

var baleToken = Environment.GetEnvironmentVariable("BALE_BOT_TOKEN");
if (string.IsNullOrWhiteSpace(baleToken))
    throw new InvalidOperationException("BALE_BOT_TOKEN is required.");

var builder = WebApplication.CreateBuilder(args);
builder.WebHost.UseUrls("http://127.0.0.1:8768");
builder.Services.AddHttpClient("bale", c => c.Timeout = TimeSpan.FromSeconds(100));
builder.Services.AddHttpClient("google-post", c => c.Timeout = TimeSpan.FromSeconds(85))
    .ConfigurePrimaryHttpMessageHandler(() => new HttpClientHandler { AllowAutoRedirect = false });
builder.Services.AddHttpClient("google-get", c => c.Timeout = TimeSpan.FromSeconds(85))
    .ConfigurePrimaryHttpMessageHandler(() => new HttpClientHandler { AllowAutoRedirect = true });
var app = builder.Build();

app.MapGet("/health", () => Results.Ok(new { success = true, provider = "bale" }));

// Opt-in Google transport for Bale mode. A non-retrying local HTTP relay,
// not an independent Google Sheets writer. Python remains owner of request IDs
// and of POST/status-confirmation/dedup decisions.
app.MapMethods("/google/forward", new[] { "GET", "POST" }, async (
    HttpContext context, IHttpClientFactory factory) =>
{
    var target = context.Request.Query["target"].ToString();
    if (!Uri.TryCreate(target, UriKind.Absolute, out var uri)
        || uri.Scheme != Uri.UriSchemeHttps
        || uri.Host != "script.google.com"
        || !uri.AbsolutePath.StartsWith("/macros/s/", StringComparison.Ordinal)
        || !uri.AbsolutePath.EndsWith("/exec", StringComparison.Ordinal))
    {
        return Results.BadRequest(new { error = "Invalid Google Apps Script destination" });
    }

    // The remaining query parameters are the original status lookup params.
    var query = context.Request.Query
        .Where(pair => pair.Key != "target")
        .SelectMany(pair => pair.Value.Select(value =>
            Uri.EscapeDataString(pair.Key) + "=" + Uri.EscapeDataString(value ?? "")));
    var suffix = string.Join("&", query);
    var remote = new UriBuilder(uri);
    remote.Query = suffix;

    using var request = new HttpRequestMessage(
        new HttpMethod(context.Request.Method), remote.Uri);
    if (HttpMethods.IsPost(context.Request.Method))
    {
        request.Content = new StreamContent(context.Request.Body);
        if (!string.IsNullOrWhiteSpace(context.Request.ContentType))
            request.Content.Headers.ContentType =
                MediaTypeHeaderValue.Parse(context.Request.ContentType);
    }
    request.Headers.TryAddWithoutValidation("Accept", "application/json");
    request.Headers.TryAddWithoutValidation("Cache-Control", "no-cache");

    try
    {
        var watch = System.Diagnostics.Stopwatch.StartNew();
        var clientName = HttpMethods.IsPost(context.Request.Method) ? "google-post" : "google-get";
        using var response = await factory.CreateClient(clientName).SendAsync(
            request, HttpCompletionOption.ResponseHeadersRead, context.RequestAborted);
        context.Response.StatusCode = (int)response.StatusCode;
        if (response.Content.Headers.ContentType is not null)
            context.Response.ContentType = response.Content.Headers.ContentType.ToString();
        if (response.Headers.Location is not null)
            context.Response.Headers.Location = response.Headers.Location.ToString();
        Console.WriteLine($"GOOGLE_RELAY method={context.Request.Method} status={(int)response.StatusCode} elapsed={watch.ElapsedMilliseconds}ms");
        await response.Content.CopyToAsync(context.Response.Body, context.RequestAborted);
    }
    catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
    {
        Console.Error.WriteLine("GOOGLE_RELAY_ERROR " + ex.GetType().Name);
        if (!context.Response.HasStarted)
        {
            context.Response.StatusCode = StatusCodes.Status502BadGateway;
            await context.Response.WriteAsJsonAsync(new { error = "Google relay upstream failure" });
        }
    }
    return Results.Empty;
});

app.Map("/{**path}", async (HttpContext context, IHttpClientFactory factory) =>
{
    var path = context.Request.Path.Value ?? "";
    bool allowed = path.StartsWith("/bot" + baleToken + "/", StringComparison.Ordinal)
        || path.StartsWith("/file/bot" + baleToken + "/", StringComparison.Ordinal);
    if (!allowed)
    {
        context.Response.StatusCode = 404;
        return;
    }

    var remote = new Uri("https://tapi.bale.ai" + path + context.Request.QueryString);
    using var request = new HttpRequestMessage(new HttpMethod(context.Request.Method), remote);
    if (context.Request.ContentLength > 0)
    {
        request.Content = new StreamContent(context.Request.Body);
        if (context.Request.ContentType != null)
            request.Content.Headers.ContentType = MediaTypeHeaderValue.Parse(context.Request.ContentType);
    }

    try
    {
        var watch = System.Diagnostics.Stopwatch.StartNew();
        using var response = await factory.CreateClient("bale").SendAsync(
            request, HttpCompletionOption.ResponseHeadersRead, context.RequestAborted);
        context.Response.StatusCode = (int)response.StatusCode;
        if (response.Content.Headers.ContentType != null)
            context.Response.ContentType = response.Content.Headers.ContentType.ToString();
        Console.WriteLine($"Bale call: status={(int)response.StatusCode}, elapsed={watch.ElapsedMilliseconds}ms");
        await response.Content.CopyToAsync(context.Response.Body, context.RequestAborted);
    }
    catch (HttpRequestException ex)
    {
        Console.Error.WriteLine("Bale upstream failed: " + ex.GetType().Name);
        context.Response.StatusCode = 502;
    }
});

await app.RunAsync();
