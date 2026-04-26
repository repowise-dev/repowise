namespace Acme.Domain;

/// <summary>A user in the system.</summary>
public record User(string Name, string Email) : IAuditable
{
    public DateTime CreatedAt { get; init; } = DateTime.UtcNow;
}

public interface IAuditable
{
    DateTime CreatedAt { get; }
}
