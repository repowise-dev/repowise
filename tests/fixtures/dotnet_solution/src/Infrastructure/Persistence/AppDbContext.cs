using Microsoft.EntityFrameworkCore;
using Acme.Domain;

namespace Acme.Infrastructure;

public class AppDbContext : DbContext
{
    public DbSet<User> Users { get; set; } = null!;

    public AppDbContext(DbContextOptions<AppDbContext> options) : base(options) { }
}
