Imports Acme.Domain
Imports Acme.Infrastructure
Imports Microsoft.AspNetCore.Builder
Imports Microsoft.EntityFrameworkCore
Imports Microsoft.Extensions.DependencyInjection

Module Program
    Sub Main(args As String())
        Dim builder = WebApplication.CreateBuilder(args)

        builder.Services.AddDbContext(Of AppDbContext)(Sub(opt) opt.UseInMemoryDatabase("test"))
        builder.Services.AddScoped(Of IUserRepository, UserRepository)()
        builder.Services.AddControllers()

        Dim app = builder.Build()

        app.MapControllers()
        app.MapGet("/health", Function() Results.Ok("alive"))

        app.Run()
    End Sub
End Module
