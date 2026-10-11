namespace Acme.Domain;

public interface IUserRepository
{
    Task<User?> FindAsync(string email);
    Task AddAsync(User user);
}
