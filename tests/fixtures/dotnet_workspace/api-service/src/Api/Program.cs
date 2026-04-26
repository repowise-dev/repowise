using Acme.Common;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddSharedServices();
var app = builder.Build();
app.Run();
