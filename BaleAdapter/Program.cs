using System.Net.Http.Headers;

var baleToken = Environment.GetEnvironmentVariable("BALE_BOT_TOKEN");
if (string.IsNullOrWhiteSpace(baleToken))
    throw new InvalidOperationException("BALE_BOT_TOKEN is required.");

var builder = WebApplication.CreateBuilder(args);
builder.WebHost.UseUrls("http://127.0.0.1:8768");
builder.Services.AddHttpClient("bale", c => c.Timeout = TimeSpan.FromSeconds(100));
var app = builder.Build();

app.MapGet("/health", () => Results.Ok(new { success = true, provider = "bale" }));

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
