using Microsoft.EntityFrameworkCore;
using Acme.Domain;

namespace Acme.Infrastructure;

public class UserRepository : IUserRepository
{
    private readonly AppDbContext _db;
    public UserRepository(AppDbContext db) { _db = db; }

    public Task<User?> FindAsync(string email) =>
        _db.Users.FirstOrDefaultAsync(u => u.Email == email);

    public Task AddAsync(User user)
    {
        _db.Users.Add(user);
        return _db.SaveChangesAsync();
    }
}
